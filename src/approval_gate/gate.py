"""Approval Gate (COMP-04).

The gate is a hard code path, not a convention: MutatingToolRegistry calls it
before checkpointing or executing anything. A gated call without a valid,
unused, payload-matching approval reference never reaches a tool.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from approval_gate.policy import ApprovalPolicy, normalize_risk_categories
from approval_gate.store import ApprovalRequest, ApprovalStore
from common.config import digest
from common.contracts import ToolContext
from common.errors import (
    ApprovalDenied,
    ApprovalRequired,
    ApprovalTimeout,
    PreconditionFailed,
)
from event_bus.publisher import EventPublisher, NullEventPublisher


def approval_digest(payload: Mapping[str, Any]) -> str:
    """Digest of the semantic payload, excluding dry-run controls.

    Approving a dry-run preview must not authorize a different live payload;
    removing only ``dry_run`` makes preview and execution share a digest while
    every operational input remains covered.
    """
    normalized = {key: value for key, value in payload.items() if key != "dry_run"}
    return digest(normalized)


@dataclass(frozen=True)
class ApprovalDecision:
    tool_name: str
    payload_digest: str
    risk_categories: frozenset[str]
    required: bool
    status: Literal["auto_approved", "approved", "pending"]
    request_id: str | None = None
    approval_ref: str | None = None

    @property
    def approved(self) -> bool:
        return self.status in {"auto_approved", "approved"}


class ApprovalGate:
    def __init__(
        self,
        policy: ApprovalPolicy,
        store: ApprovalStore,
        *,
        events: EventPublisher | None = None,
    ) -> None:
        self.policy = policy
        self.store = store
        self._events = events or NullEventPublisher()

    def evaluate(
        self,
        *,
        tool_name: str,
        payload: Mapping[str, Any],
        risk_categories: Mapping[str, Any] | frozenset[str] | set[str],
        context: ToolContext,
        rollback_plan: str = "",
    ) -> ApprovalDecision:
        categories = normalize_risk_categories(risk_categories)
        payload_digest = approval_digest(payload)
        if not self.policy.requires_approval(categories):
            if context.approval_ref:
                raise ApprovalDenied(
                    "an approval reference was supplied for an action that does not require approval"
                )
            return ApprovalDecision(
                tool_name=tool_name,
                payload_digest=payload_digest,
                risk_categories=categories,
                required=False,
                status="auto_approved",
            )

        if not context.approval_ref:
            request = self.store.create(
                tool_name=tool_name,
                payload_digest=payload_digest,
                risk_categories=categories,
                ttl_seconds=self.policy.request_ttl_seconds,
                rollback_plan=rollback_plan,
            )
            return ApprovalDecision(
                tool_name=tool_name,
                payload_digest=payload_digest,
                risk_categories=categories,
                required=True,
                status="pending",
                request_id=request.id,
            )

        request = self._validate_reference(
            context.approval_ref,
            tool_name=tool_name,
            payload_digest=payload_digest,
        )
        return ApprovalDecision(
            tool_name=tool_name,
            payload_digest=payload_digest,
            risk_categories=categories,
            required=True,
            status="approved",
            request_id=request.id,
            approval_ref=request.id,
        )

    def _validate_reference(
        self, request_id: str, *, tool_name: str, payload_digest: str
    ) -> ApprovalRequest:
        request = self.store.get(request_id)
        if request.status == "expired":
            raise ApprovalTimeout(f"approval reference {request_id} expired")
        if request.status == "pending":
            raise ApprovalRequired(
                f"approval request {request_id} is still pending", request_id=request_id
            )
        if request.status == "rejected":
            raise ApprovalDenied(f"approval request {request_id} was rejected")
        if request.status == "consumed" or request.used_at is not None:
            raise ApprovalDenied(f"approval reference {request_id} was already used")
        if request.status != "approved":
            raise ApprovalDenied(f"approval reference {request_id} is not usable")
        if request.tool_name != tool_name:
            raise ApprovalDenied("approval reference does not authorize this tool")
        if request.payload_digest != payload_digest:
            raise ApprovalDenied(
                "approval reference does not match the exact payload"
            )
        return request

    def consume(self, decision: ApprovalDecision) -> None:
        if decision.status == "auto_approved":
            return
        if not decision.approval_ref:
            raise ApprovalDenied("cannot consume an approval without a reference")
        self.store.consume(
            decision.approval_ref,
            tool_name=decision.tool_name,
            payload_digest=decision.payload_digest,
        )

    def approve(
        self,
        request_id: str,
        *,
        approver: str,
        note: str = "",
        rollback_plan: str | None = None,
    ) -> ApprovalRequest:
        request = self.store.get(request_id)
        resulting_plan = (
            request.rollback_plan if rollback_plan is None else rollback_plan.strip()
        )
        if self.policy.require_rollback_plan and not resulting_plan:
            raise PreconditionFailed(
                "approval requires a rollback plan before it can be granted"
            )
        request = self.store.decide(
            request_id,
            approved=True,
            approver=approver,
            note=note,
            rollback_plan=resulting_plan,
        )
        self._events.publish(
            "approval.granted",
            component="COMP-04",
            message=f"approval granted for {request.tool_name}",
            data={
                "tool_name": request.tool_name,
                "request_id": request.id,
                "approver": approver,
                "status": "approved",
            },
        )
        return request

    def reject(
        self, request_id: str, *, approver: str, note: str = ""
    ) -> ApprovalRequest:
        request = self.store.decide(
            request_id, approved=False, approver=approver, note=note
        )
        self._events.publish(
            "approval.rejected",
            component="COMP-04",
            message=f"approval rejected for {request.tool_name}",
            data={
                "tool_name": request.tool_name,
                "request_id": request.id,
                "approver": approver,
                "status": "rejected",
            },
            level="WARN",
        )
        return request

    def pending(self) -> list[ApprovalRequest]:
        return self.store.list_pending()
