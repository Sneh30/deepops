from __future__ import annotations

import pytest

from approval_gate.gate import ApprovalGate
from audit_log.writer import AuditLog
from checkpoint_store.store import CheckpointStore
from common.contracts import ToolContext
from common.errors import (
    ApprovalDenied,
    ApprovalRequired,
    ExecutionFailed,
    PreconditionFailed,
    ToolKindMismatch,
)
from tools_mutating.firewall import ApplyFirewallRuleTool
from tools_mutating.registry import MutatingToolRegistry
from tools_mutating.service import RestartWindowsServiceTool
from tools_readonly.registry import ReadOnlyToolRegistry
from tools_readonly.system import SystemInfoTool


def make_registry(tool, gate: ApprovalGate, store: CheckpointStore, audit: AuditLog, clock):
    return MutatingToolRegistry(
        [tool],
        gate=gate,
        checkpoints=store,
        audit_log=audit,
        clock=clock,
        sleeper=clock.sleep,
    )


def test_read_only_registry_accepts_read_only_tool() -> None:
    registry = ReadOnlyToolRegistry([SystemInfoTool()])
    result = registry.execute("get_system_info", {})
    assert result.status == "success"
    assert result.output["windows"] is False


def test_read_only_registry_rejects_mutating_tool() -> None:
    tool = RestartWindowsServiceTool(runner=object())
    with pytest.raises(ToolKindMismatch):
        ReadOnlyToolRegistry([tool])  # type: ignore[list-item]


def test_mutating_registry_rejects_read_only_tool(gate, store, audit, clock) -> None:
    registry = MutatingToolRegistry(
        [], gate=gate, checkpoints=store, audit_log=audit, clock=clock
    )
    with pytest.raises(ToolKindMismatch):
        registry.register(SystemInfoTool())  # type: ignore[arg-type]


def test_auto_approved_restart_creates_checkpoint_and_audit(
    gate, store, audit, clock, fake_runner_factory
) -> None:
    runner = fake_runner_factory(
        [
            {
                "exists": True,
                "name": "demo",
                "status": "Running",
                "binary_path": '"C:\\apps\\demo.exe"',
                "service_account": "NT AUTHORITY\\LocalService",
            },
            {"service_name": "demo", "status": "Running"},
            {
                "exists": True,
                "name": "demo",
                "status": "Running",
                "binary_path": '"C:\\apps\\demo.exe"',
                "service_account": "NT AUTHORITY\\LocalService",
            },
        ]
    )
    tool = RestartWindowsServiceTool(runner=runner)
    registry = make_registry(tool, gate, store, audit, clock)

    result = registry.execute("restart_windows_service", {"service_name": "demo"})

    assert result.status == "success"
    assert result.checkpoint_id is not None
    assert store.get(result.checkpoint_id).state["service"]["status"] == "Running"
    entries = audit.read_all()
    assert entries[0].action == "restart_windows_service"
    assert entries[0].outcome == "success"
    assert entries[0].approval_ref is None
    assert entries[0].checkpoint_id == result.checkpoint_id


def test_gated_tool_blocks_without_approval_and_creates_no_checkpoint(
    gate, store, audit, clock, fake_runner_factory
) -> None:
    runner = fake_runner_factory([])
    registry = make_registry(ApplyFirewallRuleTool(runner=runner), gate, store, audit, clock)
    payload = {
        "rule_name": "OpsPilot-Test",
        "direction": "Inbound",
        "action": "Allow",
        "protocol": "TCP",
        "local_port": "9443",
    }
    with pytest.raises(ApprovalRequired) as error:
        registry.execute("apply_firewall_rule", payload)
    assert error.value.request_id.startswith("apr-")
    assert runner.calls == []
    assert store.list_checkpoints() == []


def test_gated_tool_executes_only_with_approved_reference(
    gate, store, audit, clock, fake_runner_factory
) -> None:
    payload = {
        "rule_name": "OpsPilot-Test",
        "direction": "Inbound",
        "action": "Allow",
        "protocol": "TCP",
        "local_port": "9443",
    }
    first = gate.evaluate(
        tool_name="apply_firewall_rule",
        payload=payload,
        risk_categories={"security_sensitive"},
        context=ToolContext(),
        rollback_plan="remove rule OpsPilot-Test",
    )
    request_id = first.request_id or ""
    gate.approve(request_id, approver="human")

    runner = fake_runner_factory(
        [
            {
                "exists": False,
                "name": "OpsPilot-Test",
                "enabled": False,
                "direction": None,
                "action": None,
                "protocol": None,
                "local_port": None,
                "remote_address": None,
                "profile": None,
            },
            {
                "exists": True,
                "name": "OpsPilot-Test",
                "enabled": True,
                "direction": "Inbound",
                "action": "Allow",
                "protocol": "TCP",
                "local_port": "9443",
                "remote_address": "Any",
                "profile": "Any",
            },
            {
                "exists": True,
                "name": "OpsPilot-Test",
                "enabled": True,
                "direction": "Inbound",
                "action": "Allow",
                "protocol": "TCP",
                "local_port": "9443",
                "remote_address": "Any",
                "profile": "Any",
            },
        ]
    )
    registry = make_registry(ApplyFirewallRuleTool(runner=runner), gate, store, audit, clock)
    result = registry.execute(
        "apply_firewall_rule",
        payload,
        context=ToolContext(approval_ref=request_id),
    )
    assert result.status == "success"
    assert result.checkpoint_id
    assert audit.read_all()[0].approval_ref == request_id

    with pytest.raises(ApprovalDenied):
        registry.execute(
            "apply_firewall_rule",
            payload,
            context=ToolContext(approval_ref=request_id),
        )


def test_dry_run_never_requires_approval_or_mutates(
    gate, store, audit, clock, fake_runner_factory
) -> None:
    runner = fake_runner_factory([])
    registry = make_registry(ApplyFirewallRuleTool(runner=runner), gate, store, audit, clock)
    result = registry.execute(
        "apply_firewall_rule",
        {
            "rule_name": "OpsPilot-Test",
            "direction": "Inbound",
            "action": "Allow",
            "protocol": "TCP",
            "local_port": "9443",
            "dry_run": True,
        },
    )
    assert result.status == "preview"
    assert result.checkpoint_id is None
    assert store.list_checkpoints() == []
    assert gate.pending() == []
    assert runner.calls == []
    entry = audit.read_all()[0]
    assert entry.evidence["dry_run"] is True
    assert entry.approval_ref is None


def test_verification_failure_triggers_rollback(
    gate, store, audit, clock, fake_runner_factory
) -> None:
    runner = fake_runner_factory(
        [
            {"exists": True, "name": "demo", "status": "Running"},
            {"service_name": "demo", "status": "Running"},
            {"exists": True, "name": "demo", "status": "Stopped"},
        ]
    )
    registry = make_registry(
        RestartWindowsServiceTool(runner=runner), gate, store, audit, clock
    )
    result = registry.execute("restart_windows_service", {"service_name": "demo"})
    assert result.status == "rolled_back"
    assert result.evidence["rollback_succeeded"] is True
    assert "run_script" in runner.calls
    entry = audit.read_all()[0]
    assert entry.outcome == "rolled_back"


def test_execution_failure_retries_with_bounded_backoff(
    gate, store, audit, clock, fake_runner_factory
) -> None:
    runner = fake_runner_factory(
        [
            {"exists": True, "name": "demo", "status": "Running"},
            ExecutionFailed("transient"),
            {"service_name": "demo", "status": "Running"},
            {"exists": True, "name": "demo", "status": "Running"},
        ]
    )
    registry = make_registry(
        RestartWindowsServiceTool(runner=runner), gate, store, audit, clock
    )
    result = registry.execute("restart_windows_service", {"service_name": "demo"})
    assert result.status == "success"
    assert result.attempt_count == 2
    assert clock.sleeps == [5.0]


def test_capture_state_failure_prevents_execution(
    gate, store, audit, clock, fake_runner_factory
) -> None:
    runner = fake_runner_factory([PreconditionFailed("cannot read service state")])
    registry = make_registry(
        RestartWindowsServiceTool(runner=runner), gate, store, audit, clock
    )
    with pytest.raises(PreconditionFailed):
        registry.execute("restart_windows_service", {"service_name": "demo"})
    assert runner.calls == ["run_json"]
    assert store.list_checkpoints() == []
    assert audit.read_all() == []
