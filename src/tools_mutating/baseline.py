"""Baseline host hardening tool (COMP-09, FR-060).

Hardening is security-sensitive by definition and therefore always routed
through the approval gate. The PowerShell module is idempotent and implements
``SupportsShouldProcess``; this tool's preview path is still pure Python so it
can be reviewed without touching the host.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from common.contracts import MutatingTool, ToolResult, mutating_contract, result_schema
from common.errors import PreconditionFailed, VerificationFailed
from common.process import ps_quote
from tools_mutating.base import (
    PowerShellTool,
    encode_state,
    json_pipeline,
    module_script,
    ps_array,
    ps_bool,
)


class ApplyHardeningTool(PowerShellTool, MutatingTool):
    def __init__(self, runner: Any = None) -> None:
        self._runner = runner
        self.contract = mutating_contract(
            name="apply_host_hardening",
            description=(
                "Apply the documented Windows baseline: firewall profiles, explicit inbound "
                "allows, RDP policy, and TLS 1.2+ enforcement. Requires human approval."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "allowed_inbound_tcp_ports",
                    "metrics_scrape_cidrs",
                    "rdp_enabled",
                ],
                "properties": {
                    "allowed_inbound_tcp_ports": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1, "maximum": 65535},
                        "uniqueItems": True,
                    },
                    "metrics_scrape_cidrs": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "uniqueItems": True,
                    },
                    "rdp_enabled": {"type": "boolean"},
                    "service_account": {"type": "string", "minLength": 1},
                },
            },
            output_schema=result_schema(
                {
                    "applied": {"type": "boolean"},
                    "compliant": {"type": "boolean"},
                    "findings": {"type": "array", "items": {"type": "string"}},
                }
            ),
            timeout_seconds=300,
            risk_categories=("security_sensitive",),
            verification=("Test-OpsPilotBaseline reports compliant with zero findings",),
            max_retries=0,
        )

    def preview(self, payload: Mapping[str, Any]) -> ToolResult:
        return ToolResult(
            status="preview",
            output={"applied": False, "compliant": False, "findings": []},
            evidence={
                "would_enable_firewall_profiles": ["Domain", "Private", "Public"],
                "would_set_default_inbound": "Block",
                "would_allow_tcp_ports": list(payload["allowed_inbound_tcp_ports"]),
                "would_restrict_metrics_to": list(payload["metrics_scrape_cidrs"]),
                "would_set_rdp_enabled": bool(payload["rdp_enabled"]),
                "would_enforce_tls12": True,
            },
        )

    def _state(self) -> dict[str, Any]:
        output = self.run_json(
            "OpsPilot.Baseline",
            json_pipeline("Get-OpsPilotBaselineState"),
            self.contract.timeout_seconds,
        )
        if not isinstance(output, dict):
            raise PreconditionFailed("baseline state did not return a JSON object")
        return output

    def capture_state(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {"baseline": self._state()}

    def apply(self, payload: Mapping[str, Any]) -> ToolResult:
        ports = ps_array([str(port) for port in payload["allowed_inbound_tcp_ports"]])
        cidrs = ps_array([str(cidr) for cidr in payload["metrics_scrape_cidrs"]])
        service_account = payload.get("service_account")
        account_arg = (
            f" -ServiceAccount {ps_quote(str(service_account))}" if service_account else ""
        )
        body = json_pipeline(
            "Set-OpsPilotBaseline "
            f"-AllowedInboundTcpPorts {ports} "
            f"-MetricsScrapeCidrs {cidrs} "
            f"-RdpEnabled:{ps_bool(bool(payload['rdp_enabled']))}"
            f"{account_arg}"
        )
        output = self.runner().run_json(
            module_script("OpsPilot.Baseline", body),
            timeout_seconds=self.contract.timeout_seconds,
        )
        if not isinstance(output, dict):
            raise PreconditionFailed("baseline apply did not return a JSON object")
        return ToolResult(
            status="success",
            output={
                "applied": bool(output.get("applied", True)),
                "compliant": bool(output.get("compliant", False)),
                "findings": list(output.get("findings", [])),
            },
            evidence={"module": "OpsPilot.Baseline"},
        )

    def verify(self, payload: Mapping[str, Any], result: ToolResult) -> None:
        ports = ps_array([str(port) for port in payload["allowed_inbound_tcp_ports"]])
        cidrs = ps_array([str(cidr) for cidr in payload["metrics_scrape_cidrs"]])
        output = self.run_json(
            "OpsPilot.Baseline",
            json_pipeline(
                "Test-OpsPilotBaseline "
                f"-AllowedInboundTcpPorts {ports} "
                f"-MetricsScrapeCidrs {cidrs} "
                f"-RdpEnabled:{ps_bool(bool(payload['rdp_enabled']))}"
            ),
            self.contract.timeout_seconds,
        )
        if not isinstance(output, dict) or not output.get("compliant"):
            raise VerificationFailed(
                "baseline compliance check failed after hardening",
                details={"result": output},
            )
        result.evidence["verified"] = output

    def restore(self, state: Mapping[str, Any]) -> None:
        baseline_state = state.get("baseline")
        if not isinstance(baseline_state, dict):
            raise PreconditionFailed("checkpoint has no baseline state")
        self.runner().run_script(
            module_script(
                "OpsPilot.Baseline",
                json_pipeline(
                    "Restore-OpsPilotBaselineState "
                    f"-StateBase64 {ps_quote(encode_state(baseline_state))}"
                ),
            ),
            timeout_seconds=self.contract.timeout_seconds,
        )
