from __future__ import annotations

import pytest

from approval_gate.gate import ApprovalGate
from approval_gate.store import SqliteApprovalStore
from common.contracts import ToolContext
from common.errors import ApprovalDenied, ApprovalTimeout, PreconditionFailed


def test_low_risk_action_auto_clears(gate: ApprovalGate) -> None:
    decision = gate.evaluate(
        tool_name="restart_windows_service",
        payload={"service_name": "demo"},
        risk_categories=set(),
        context=ToolContext(),
    )
    assert decision.status == "auto_approved"
    assert decision.required is False
    assert decision.approval_ref is None
    gate.consume(decision)  # no-op, must not raise


def test_gated_action_without_reference_is_pending(gate: ApprovalGate) -> None:
    decision = gate.evaluate(
        tool_name="apply_firewall_rule",
        payload={"rule_name": "x"},
        risk_categories={"security_sensitive"},
        context=ToolContext(),
        rollback_plan="remove the rule",
    )
    assert decision.status == "pending"
    assert decision.request_id
    assert gate.pending()[0].id == decision.request_id


def test_gated_action_requires_rollback_plan(gate: ApprovalGate) -> None:
    decision = gate.evaluate(
        tool_name="apply_firewall_rule",
        payload={"rule_name": "x"},
        risk_categories={"security_sensitive"},
        context=ToolContext(),
    )
    with pytest.raises(PreconditionFailed):
        gate.approve(decision.request_id or "", approver="human")


def test_approve_then_use_reference(gate: ApprovalGate) -> None:
    decision = gate.evaluate(
        tool_name="apply_firewall_rule",
        payload={"rule_name": "x"},
        risk_categories={"security_sensitive"},
        context=ToolContext(),
        rollback_plan="delete rule x",
    )
    request_id = decision.request_id or ""
    approved = gate.approve(request_id, approver="human", note="ok")
    assert approved.status == "approved"

    authorized = gate.evaluate(
        tool_name="apply_firewall_rule",
        payload={"rule_name": "x"},
        risk_categories={"security_sensitive"},
        context=ToolContext(approval_ref=request_id),
    )
    assert authorized.status == "approved"
    gate.consume(authorized)

    with pytest.raises(ApprovalDenied):
        gate.evaluate(
            tool_name="apply_firewall_rule",
            payload={"rule_name": "x"},
            risk_categories={"security_sensitive"},
            context=ToolContext(approval_ref=request_id),
        )


def test_reference_is_bound_to_tool_and_payload(gate: ApprovalGate) -> None:
    decision = gate.evaluate(
        tool_name="apply_firewall_rule",
        payload={"rule_name": "x"},
        risk_categories={"security_sensitive"},
        context=ToolContext(),
        rollback_plan="delete rule x",
    )
    request_id = decision.request_id or ""
    gate.approve(request_id, approver="human")

    with pytest.raises(ApprovalDenied):
        gate.evaluate(
            tool_name="apply_firewall_rule",
            payload={"rule_name": "y"},
            risk_categories={"security_sensitive"},
            context=ToolContext(approval_ref=request_id),
        )
    with pytest.raises(ApprovalDenied):
        gate.evaluate(
            tool_name="remove_windows_service",
            payload={"service_name": "x"},
            risk_categories={"destructive"},
            context=ToolContext(approval_ref=request_id),
        )


def test_rejected_reference_is_denied(gate: ApprovalGate) -> None:
    decision = gate.evaluate(
        tool_name="apply_firewall_rule",
        payload={"rule_name": "x"},
        risk_categories={"security_sensitive"},
        context=ToolContext(),
        rollback_plan="delete rule x",
    )
    request_id = decision.request_id or ""
    gate.reject(request_id, approver="human", note="not now")
    with pytest.raises(ApprovalDenied):
        gate.evaluate(
            tool_name="apply_firewall_rule",
            payload={"rule_name": "x"},
            risk_categories={"security_sensitive"},
            context=ToolContext(approval_ref=request_id),
        )


def test_expired_request_is_not_approvable(
    gate: ApprovalGate, approval_store: SqliteApprovalStore, clock
) -> None:
    decision = gate.evaluate(
        tool_name="apply_firewall_rule",
        payload={"rule_name": "x"},
        risk_categories={"security_sensitive"},
        context=ToolContext(),
        rollback_plan="delete rule x",
    )
    clock.advance(4000)
    approval_store.expire_overdue()
    with pytest.raises(ApprovalTimeout):
        gate.approve(decision.request_id or "", approver="human")


def test_unknown_risk_category_rejected(gate: ApprovalGate) -> None:
    with pytest.raises(ValueError):
        gate.evaluate(
            tool_name="x",
            payload={},
            risk_categories={"made_up"},
            context=ToolContext(),
        )


def test_approval_reference_rejected_for_auto_cleared_action(gate: ApprovalGate) -> None:
    decision = gate.evaluate(
        tool_name="apply_firewall_rule",
        payload={"rule_name": "x"},
        risk_categories={"security_sensitive"},
        context=ToolContext(),
        rollback_plan="delete rule x",
    )
    request_id = decision.request_id or ""
    gate.approve(request_id, approver="human")
    with pytest.raises(ApprovalDenied):
        gate.evaluate(
            tool_name="restart_windows_service",
            payload={"service_name": "demo"},
            risk_categories=set(),
            context=ToolContext(approval_ref=request_id),
        )
