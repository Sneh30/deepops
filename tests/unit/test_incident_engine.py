from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from checkpoint_store.store import CheckpointStore
from common.contracts import ToolContext, ToolResult
from common.errors import ApprovalRequired
from deployment_engine.health import HealthOutcome
from deployment_engine.models import HealthTarget
from incident_engine.alerts import AlertEvaluator, AlertRule, MetricSnapshot
from incident_engine.drift import DriftDetector
from incident_engine.engine import IncidentEngine
from incident_engine.escalation import (
    EmailMessage,
    EscalationNotifier,
    NotificationConfig,
)
from incident_engine.health import HealthPoller, HealthTargetSpec, PollResult
from incident_engine.remediation import RemediationExecutor, RemediationOutcome


class FakeChecker:
    def __init__(self, results: list[tuple[bool, str | None]]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    def check(self, target: HealthTarget, *, port: int | None = None) -> tuple[bool, str | None]:
        self.calls.append({"target": target, "port": port})
        return self.results.pop(0)

    def wait(self, target: HealthTarget, *, port: int | None, window_seconds: int) -> HealthOutcome:
        healthy, _ = self.check(target, port=port)
        return HealthOutcome(healthy, 1, 1 if healthy else 0, 0 if healthy else 1, 1.0)


def target(name: str = "app") -> HealthTargetSpec:
    return HealthTargetSpec(
        name=name,
        app=name,
        service=f"{name}-green",
        health=HealthTarget(kind="http", url=f"http://127.0.0.1:8080/{name}"),
        port=8081,
        failure_threshold=2,
    )


def test_alert_only_fires_after_for_duration(clock) -> None:
    evaluator = AlertEvaluator(
        [
            AlertRule(
                name="high-cpu",
                severity="warning",
                metric="cpu_percent",
                operator="gt",
                threshold=85,
                for_seconds=300,
            )
        ],
        clock=clock,
    )
    snapshot = MetricSnapshot(values={"cpu_percent": 90.0})
    assert evaluator.evaluate(snapshot) == []
    clock.advance(299)
    assert evaluator.evaluate(snapshot) == []
    clock.advance(1)
    firing = evaluator.evaluate(snapshot)
    assert len(firing) == 1
    assert firing[0].name == "high-cpu"
    assert evaluator.evaluate(snapshot) == []


def test_alert_resets_when_condition_clears(clock) -> None:
    evaluator = AlertEvaluator(
        [
            AlertRule(
                name="disk-pressure",
                severity="critical",
                metric="disk_percent",
                operator="gt",
                threshold=90,
                for_seconds=60,
            )
        ],
        clock=clock,
    )
    hot = MetricSnapshot(values={"disk_percent": 95.0})
    cool = MetricSnapshot(values={"disk_percent": 50.0})
    assert evaluator.evaluate(hot) == []
    clock.advance(30)
    assert evaluator.evaluate(cool) == []
    clock.advance(1)
    assert evaluator.evaluate(hot) == []


def test_health_poller_triggers_after_threshold(store: CheckpointStore, clock) -> None:
    checker = FakeChecker([(False, "refused"), (False, "refused"), (True, None)])
    poller = HealthPoller(
        store, [target()], checker, clock=clock, interval_seconds=10  # type: ignore[arg-type]
    )
    first = poller.poll_once()[0]
    assert first.triggered is False
    second = poller.poll_once()[0]
    assert second.triggered is True
    third = poller.poll_once()[0]
    assert third.triggered is False
    summary = store.health_summary(start=clock.now(), end=clock.now(), service="app-green")
    assert summary["samples"] == 3


def test_health_poller_rejects_out_of_range_interval(store: CheckpointStore, clock) -> None:
    with pytest.raises(ValueError):
        HealthPoller(store, [], FakeChecker([]), clock=clock, interval_seconds=61)  # type: ignore[arg-type]


class FakeRegistry:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    def execute(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        context: ToolContext | None = None,
        rollback_plan: str = "",
    ) -> ToolResult:
        self.calls.append((name, payload))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_remediation_resolves_after_restart_and_healthy_check(clock) -> None:
    registry = FakeRegistry(
        [ToolResult(status="success", output={"service_name": "app-green"})]
    )
    checker = FakeChecker([(True, None)])
    executor = RemediationExecutor(
        registry,  # type: ignore[arg-type]
        checker,  # type: ignore[arg-type]
        clock=clock,
        max_attempts=2,
        backoff_seconds=1,
    )
    outcome = executor.remediate(target())
    assert outcome.status == "resolved"
    assert outcome.attempts == 1
    assert registry.calls[0][0] == "restart_windows_service"


def test_remediation_escalates_when_approval_required(clock) -> None:
    registry = FakeRegistry([ApprovalRequired("needs approval", request_id="apr-9")])
    executor = RemediationExecutor(
        registry,  # type: ignore[arg-type]
        FakeChecker([]),  # type: ignore[arg-type]
        clock=clock,
    )
    outcome = executor.remediate(target())
    assert outcome.status == "escalated"
    assert outcome.approval_request_id == "apr-9"


def test_remediation_exhausts_attempts(clock) -> None:
    registry = FakeRegistry(
        [
            ToolResult(status="success", output={}),
            ToolResult(status="success", output={}),
        ]
    )
    checker = FakeChecker([(False, "still down"), (False, "still down")])
    executor = RemediationExecutor(
        registry,  # type: ignore[arg-type]
        checker,  # type: ignore[arg-type]
        clock=clock,
        max_attempts=2,
        backoff_seconds=1,
    )
    outcome = executor.remediate(target())
    assert outcome.status == "failed"
    assert outcome.attempts == 2
    assert clock.sleeps == [1.0]


class FakeNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    def escalate(self, incident, diagnostics):  # type: ignore[no-untyped-def]
        self.calls.append((incident.id, diagnostics))
        return type(
            "Result",
            (),
            {
                "sent": True,
                "subject": "subject",
                "recipients": 1,
                "error": None,
                "payload": {},
            },
        )()


def test_incident_engine_resolves_and_audits(store: CheckpointStore, clock, audit) -> None:
    result = PollResult(
        target=target(),
        healthy=False,
        error="refused",
        consecutive_failures=2,
        triggered=True,
        details={"checked_at": clock.now().isoformat()},
    )

    class FakePoller:
        def poll_once(self) -> list[PollResult]:
            return [result]

        def reset(self, name: str) -> None:
            pass

    class FakeRemediator:
        def remediate(self, target):
            return RemediationOutcome(
                status="resolved", attempts=1, evidence={"attempts": [1]}
            )

    engine = IncidentEngine(
        store,
        FakePoller(),  # type: ignore[arg-type]
        FakeRemediator(),  # type: ignore[arg-type]
        audit=audit,
        id_factory=lambda: "inc-test",
    )
    incidents = engine.poll_once()
    assert len(incidents) == 1
    assert incidents[0].status == "Resolved"
    assert incidents[0].resolved_at is not None
    transitions = [entry for entry in audit.read_all() if entry.action == "incident_transition"]
    assert [entry.inputs["to"] for entry in transitions] == ["Remediation-Attempted", "Resolved"]


def test_incident_engine_escalates_and_notifies(store: CheckpointStore, clock, audit) -> None:
    result = PollResult(
        target=target(),
        healthy=False,
        error="refused",
        consecutive_failures=2,
        triggered=True,
        details={},
    )

    class FakePoller:
        def poll_once(self) -> list[PollResult]:
            return [result]

        def reset(self, name: str) -> None:
            pass

    class FakeRemediator:
        def remediate(self, target):
            return RemediationOutcome(
                status="failed", attempts=2, evidence={"attempts": [1, 2]}
            )

    notifier = FakeNotifier()
    engine = IncidentEngine(
        store,
        FakePoller(),  # type: ignore[arg-type]
        FakeRemediator(),  # type: ignore[arg-type]
        notifier=notifier,  # type: ignore[arg-type]
        audit=audit,
        id_factory=lambda: "inc-fail",
    )
    incidents = engine.poll_once()
    assert incidents[0].status == "Escalated"
    assert notifier.calls[0][0] == "inc-fail"
    assert any(entry.action == "incident_escalation" for entry in audit.read_all())


class FakeTransport:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.messages: list[EmailMessage] = []
        self.passwords: list[str | None] = []

    def send(self, message: EmailMessage, *, password: str | None) -> None:
        if self.error is not None:
            raise self.error
        self.messages.append(message)
        self.passwords.append(password)


def notification_config() -> NotificationConfig:
    return NotificationConfig(
        recipients=("ops@example.invalid",),
        from_address="opspilot@example.invalid",
        smtp_host="smtp.example.invalid",
        smtp_port=587,
        use_starttls=True,
        credential_ref="smtp-primary",
    )


def test_escalation_redacts_secrets(store: CheckpointStore) -> None:
    incident = store.create_incident(
        app="app",
        service="app-green",
        summary="down",
        details={"password": "hunter2", "reason": "timeout"},
        incident_id="inc-redact",
    )
    transport = FakeTransport()
    notifier = EscalationNotifier(
        notification_config(), transport=transport, credential_provider=lambda ref: "smtp-password"
    )
    result = notifier.escalate(incident, {"api_key": "top-secret"})
    assert result.sent is True
    assert transport.passwords == ["smtp-password"]
    body = transport.messages[0].body
    assert "hunter2" not in body
    assert "top-secret" not in body


class FakeProvider:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state

    def live_state(self) -> Mapping[str, Any]:
        return self.state


def compliant_state() -> dict[str, Any]:
    return {
        "firewall_profiles": [
            {
                "name": "Domain",
                "enabled": True,
                "default_inbound": "Block",
                "default_outbound": "Allow",
            },
            {
                "name": "Private",
                "enabled": True,
                "default_inbound": "Block",
                "default_outbound": "Allow",
            },
            {
                "name": "Public",
                "enabled": True,
                "default_inbound": "Block",
                "default_outbound": "Allow",
            },
        ],
        "rdp_enabled": False,
        "tls": [
            {"protocol": "TLS 1.2", "role": "Server", "enabled": 1},
            {"protocol": "TLS 1.0", "role": "Server", "enabled": 0},
            {"protocol": "TLS 1.0", "role": "Client", "enabled": 0},
            {"protocol": "TLS 1.1", "role": "Server", "enabled": 0},
            {"protocol": "TLS 1.1", "role": "Client", "enabled": 0},
        ],
        "managed_rules": [
            {
                "name": "OpsPilot-Allow-80",
                "enabled": True,
                "direction": "Inbound",
                "action": "Allow",
                "protocol": "TCP",
                "local_port": "80",
                "remote_address": "Any",
                "profile": "Any",
            },
            {
                "name": "OpsPilot-Allow-443",
                "enabled": True,
                "direction": "Inbound",
                "action": "Allow",
                "protocol": "TCP",
                "local_port": "443",
                "remote_address": "Any",
                "profile": "Any",
            },
            {
                "name": "OpsPilot-Metrics-9182",
                "enabled": True,
                "direction": "Inbound",
                "action": "Allow",
                "protocol": "TCP",
                "local_port": "9182",
                "remote_address": "127.0.0.1/32",
                "profile": "Any",
            },
        ],
    }


def host_document() -> dict[str, Any]:
    return {
        "host": {"rdp_enabled": False},
        "network": {
            "allowed_inbound_tcp_ports": [80, 443],
            "metrics_scrape_cidrs": ["127.0.0.1/32"],
        },
    }


def test_drift_detects_compliant_state() -> None:
    report = DriftDetector(FakeProvider(compliant_state())).check(host_document())
    assert report.compliant is True
    assert report.to_dict()["findings"] == []


def test_drift_detects_disabled_firewall_and_missing_rule() -> None:
    state = compliant_state()
    state["firewall_profiles"][0]["enabled"] = False
    state["managed_rules"] = state["managed_rules"][1:]
    report = DriftDetector(FakeProvider(state)).check(host_document())
    assert report.compliant is False
    resources = {finding.resource for finding in report.findings}
    assert "firewall.profile.domain.enabled" in resources
    assert "firewall.rule.OpsPilot-Allow-80" in resources


def test_drift_detects_tls_drift_and_rdp() -> None:
    state = compliant_state()
    state["rdp_enabled"] = True
    state["tls"][0] = {"protocol": "TLS 1.2", "role": "Server", "enabled": 0}
    report = DriftDetector(FakeProvider(state)).check(host_document())
    resources = {finding.resource for finding in report.findings}
    assert "rdp.enabled" in resources
    assert "tls.1.2.server.enabled" in resources
