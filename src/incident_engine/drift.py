"""Configuration drift detection (COMP-07/COMP-09, FR-061).

Declared host intent (host.yaml) is compared with live Windows baseline state.
The detector only reports drift; correction goes through the normal approval
gate if the corrective action is itself gated.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from common.process import PowerShellRunner
from tools_mutating.base import json_pipeline, module_script

BASELINE_MODULE = "OpsPilot.Baseline"


class LiveStateProvider(Protocol):
    def live_state(self) -> Mapping[str, Any]: ...


class PowerShellBaselineProvider:
    def __init__(self, runner: PowerShellRunner | None = None) -> None:
        self._runner = runner

    def live_state(self) -> Mapping[str, Any]:
        runner = self._runner or PowerShellRunner()
        output = runner.run_json(
            module_script(BASELINE_MODULE, json_pipeline("Get-OpsPilotBaselineState")),
            timeout_seconds=120,
        )
        if not isinstance(output, dict):
            raise ValueError("baseline state did not return a JSON object")
        return output


@dataclass(frozen=True)
class DriftFinding:
    resource: str
    expected: Any
    actual: Any
    severity: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "expected": self.expected,
            "actual": self.actual,
            "severity": self.severity,
            "description": self.description,
        }


@dataclass(frozen=True)
class DriftReport:
    findings: tuple[DriftFinding, ...] = field(default_factory=tuple)
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def compliant(self) -> bool:
        return not self.findings

    @property
    def critical(self) -> bool:
        return any(finding.severity == "critical" for finding in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "compliant": self.compliant,
            "critical": self.critical,
            "checked_at": self.checked_at.isoformat(),
            "findings": [finding.to_dict() for finding in self.findings],
        }


class DriftDetector:
    def __init__(self, provider: LiveStateProvider) -> None:
        self._provider = provider

    def check(self, document: Mapping[str, Any]) -> DriftReport:
        host = document.get("host", {})
        network = document.get("network", {})
        if not isinstance(host, Mapping) or not isinstance(network, Mapping):
            raise ValueError("host config must contain host and network objects")
        live = self._provider.live_state()
        findings: list[DriftFinding] = []

        profiles = live.get("firewall_profiles", [])
        profile_map = {
            str(profile.get("name", "")).lower(): profile
            for profile in profiles
            if isinstance(profile, Mapping)
        }
        for profile_name in ("domain", "private", "public"):
            profile = profile_map.get(profile_name)
            if profile is None:
                findings.append(
                    DriftFinding(
                        f"firewall.profile.{profile_name}",
                        "present/enabled/default-deny",
                        "missing",
                        "critical",
                        f"Firewall profile {profile_name} is missing.",
                    )
                )
                continue
            if not profile.get("enabled"):
                findings.append(
                    DriftFinding(
                        f"firewall.profile.{profile_name}.enabled",
                        True,
                        profile.get("enabled"),
                        "critical",
                        f"Firewall profile {profile_name} is disabled.",
                    )
                )
            if str(profile.get("default_inbound", "")).lower() != "block":
                findings.append(
                    DriftFinding(
                        f"firewall.profile.{profile_name}.default_inbound",
                        "Block",
                        profile.get("default_inbound"),
                        "critical",
                        f"Firewall profile {profile_name} does not default-deny inbound.",
                    )
                )

        rules = {
            str(rule.get("name", "")): rule
            for rule in live.get("managed_rules", [])
            if isinstance(rule, Mapping)
        }
        expected_rules = {f"OpsPilot-Allow-{port}" for port in network.get("allowed_inbound_tcp_ports", [])}
        for name in sorted(expected_rules):
            rule = rules.get(name)
            if rule is None or not rule.get("enabled"):
                findings.append(
                    DriftFinding(
                        f"firewall.rule.{name}",
                        "present/enabled",
                        "missing" if rule is None else "disabled",
                        "critical",
                        f"Required inbound allow-list entry {name} is missing or disabled.",
                    )
                )

        metrics_cidrs = [str(cidr) for cidr in network.get("metrics_scrape_cidrs", [])]
        metrics_rule = rules.get("OpsPilot-Metrics-9182")
        if metrics_cidrs:
            if metrics_rule is None or not metrics_rule.get("enabled"):
                findings.append(
                    DriftFinding(
                        "firewall.rule.OpsPilot-Metrics-9182",
                        "present/enabled",
                        "missing" if metrics_rule is None else "disabled",
                        "critical",
                        "Metrics scrape rule is missing or disabled.",
                    )
                )
            elif metrics_rule is not None:
                actual_cidrs = sorted(
                    part.strip()
                    for part in str(metrics_rule.get("remote_address", "")).split(",")
                    if part.strip()
                )
                if actual_cidrs != sorted(metrics_cidrs):
                    findings.append(
                        DriftFinding(
                            "firewall.rule.OpsPilot-Metrics-9182.remote_address",
                            sorted(metrics_cidrs),
                            actual_cidrs,
                            "critical",
                            "Metrics scrape source CIDRs drifted from declared intent.",
                        )
                    )

        expected_rdp = bool(host.get("rdp_enabled", False))
        if bool(live.get("rdp_enabled", False)) != expected_rdp:
            findings.append(
                DriftFinding(
                    "rdp.enabled",
                    expected_rdp,
                    bool(live.get("rdp_enabled", False)),
                    "critical",
                    "RDP enabled state differs from declared host configuration.",
                )
            )

        tls = {
            (str(item.get("protocol", "")), str(item.get("role", ""))): item
            for item in live.get("tls", [])
            if isinstance(item, Mapping)
        }
        tls12 = tls.get(("TLS 1.2", "Server"))
        if tls12 is None or tls12.get("enabled") != 1:
            findings.append(
                DriftFinding(
                    "tls.1.2.server.enabled",
                    1,
                    None if tls12 is None else tls12.get("enabled"),
                    "critical",
                    "TLS 1.2 server protocol is not enabled.",
                )
            )
        for protocol in ("TLS 1.0", "TLS 1.1"):
            for role in ("Server", "Client"):
                item = tls.get((protocol, role))
                if item is not None and item.get("enabled") == 1:
                    findings.append(
                        DriftFinding(
                            f"tls.{protocol}.{role}.enabled",
                            0,
                            1,
                            "critical",
                            f"{protocol} {role} is still enabled.",
                        )
                    )

        for name in sorted(set(rules) - expected_rules - {"OpsPilot-Metrics-9182"}):
            findings.append(
                DriftFinding(
                    f"firewall.rule.{name}",
                    "absent",
                    "present",
                    "warning",
                    f"Unexpected OpsPilot-managed firewall rule {name} is present.",
                )
            )

        return DriftReport(findings=tuple(findings))
