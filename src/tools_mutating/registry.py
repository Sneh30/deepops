"""Mutating tool registry (COMP-03).

Hard guarantees enforced here, not by convention (SAS.md §5):
  * every live call is evaluated by COMP-04 before checkpointing/execution;
  * every live call takes a COMP-05 checkpoint before execution;
  * dry-run uses a distinct preview path and never takes a checkpoint or mutates;
  * retries are bounded by the contract and only for declared error modes;
  * post-execution verification must pass before success is returned;
  * a failed execution restores the pre-mutation checkpoint;
  * every call writes a COMP-12 audit record with outcome and evidence.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from approval_gate.gate import ApprovalGate
from audit_log.writer import AuditLog
from checkpoint_store.store import CheckpointStore
from common.clock import Clock, SystemClock
from common.config import validate_document
from common.contracts import MutatingTool, ToolContext, ToolResult
from common.errors import (
    ApprovalRequired,
    CheckpointFailed,
    ExecutionFailed,
    OpsPilotError,
    TimeoutExceeded,
    ToolKindMismatch,
    ToolNotFound,
    VerificationFailed,
)
from common.log import StructuredLogger
from event_bus.publisher import EventPublisher, NullEventPublisher


class MutatingToolRegistry:
    def __init__(
        self,
        tools: Iterable[MutatingTool] = (),
        *,
        gate: ApprovalGate,
        checkpoints: CheckpointStore,
        audit_log: AuditLog | None = None,
        logger: StructuredLogger | None = None,
        clock: Clock | None = None,
        sleeper: Callable[[float], None] | None = None,
        events: EventPublisher | None = None,
    ) -> None:
        self._tools: dict[str, MutatingTool] = {}
        self._gate = gate
        self._checkpoints = checkpoints
        self._audit = audit_log
        self._logger = logger or StructuredLogger("COMP-03")
        self._clock = clock or SystemClock()
        self._sleep = sleeper or self._clock.sleep
        self._events = events or NullEventPublisher()
        for tool in tools:
            self.register(tool)

    def register(self, tool: MutatingTool) -> None:
        contract = tool.contract
        if contract.kind != "mutating":
            raise ToolKindMismatch(
                f"tool {contract.name} is {contract.kind}; cannot register in mutating registry"
            )
        if not contract.requires_checkpoint or not contract.requires_approval_evaluation:
            raise ValueError(
                f"mutating tool {contract.name} must require checkpoint and approval evaluation"
            )
        if contract.name in self._tools:
            raise ValueError(f"duplicate mutating tool: {contract.name}")
        contract.validate()
        self._tools[contract.name] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def describe(self) -> list[dict[str, Any]]:
        return [self._tools[name].contract.to_dict() for name in self.names()]

    def get(self, name: str) -> MutatingTool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFound(f"mutating tool not found: {name}")
        return tool

    def execute(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        context: ToolContext | None = None,
        rollback_plan: str = "",
    ) -> ToolResult:
        tool = self.get(name)
        contract = tool.contract
        contract.validate_payload(payload)
        tool_context = context or ToolContext()
        dry_run = bool(payload.get("dry_run", False))
        risk_categories = tool.assess_risk(payload, tool_context)
        started = time.monotonic()

        if dry_run:
            # FR-091: preview is a distinct path. No approval, checkpoint, or
            # mutation occurs; the tool's preview() implementation must not
            # change host state.
            preview_result = tool.preview(payload)
            if not isinstance(preview_result, ToolResult):
                raise ExecutionFailed(f"tool {name} preview returned a non-ToolResult")
            preview_result.status = "preview"
            preview_result.checkpoint_id = None
            preview_result.attempt_count = 0
            validate_document(preview_result.output, contract.output_schema)
            preview_result.duration_ms = int((time.monotonic() - started) * 1000)
            self._write_audit(
                action=name,
                inputs=payload,
                outcome="success",
                checkpoint_id=None,
                approval_ref=None,
                evidence={
                    "dry_run": True,
                    "risk_categories": sorted(risk_categories),
                    "duration_ms": preview_result.duration_ms,
                    **preview_result.evidence,
                },
            )
            self._events.publish(
                "tool.previewed",
                component="COMP-03",
                message=f"previewed mutating tool: {name}",
                data={
                    "tool": name,
                    "status": "preview",
                    "duration_ms": preview_result.duration_ms,
                    "risk_categories": sorted(risk_categories),
                },
            )
            return preview_result

        decision = self._gate.evaluate(
            tool_name=name,
            payload=payload,
            risk_categories=risk_categories,
            context=tool_context,
            rollback_plan=rollback_plan,
        )
        if decision.status == "pending":
            # No checkpoint and no execution on this path. The caller can show
            # the request ID to the human and retry with an approval reference.
            self._events.publish(
                "approval.requested",
                component="COMP-04",
                message=f"approval required for {name}",
                data={
                    "tool_name": name,
                    "request_id": decision.request_id,
                    "risk_categories": sorted(risk_categories),
                    "rollback_plan": rollback_plan,
                },
                level="WARN",
            )
            self._events.publish(
                "tool.blocked",
                component="COMP-03",
                message=f"tool {name} blocked pending approval",
                data={"tool": name, "request_id": decision.request_id},
                level="WARN",
            )
            raise ApprovalRequired(
                f"tool {name} requires human approval before execution",
                request_id=decision.request_id or "unknown",
            )
        if decision.request_id:
            self._events.publish(
                "approval.granted",
                component="COMP-04",
                message=f"approval granted for {name}",
                data={
                    "tool_name": name,
                    "request_id": decision.request_id,
                    "status": decision.status,
                    "approval_ref": decision.approval_ref,
                },
            )

        try:
            state = tool.capture_state(payload)
        except OpsPilotError:
            raise
        except Exception as error:
            raise CheckpointFailed(
                f"pre-mutation state capture failed for {name}",
                details={"error": str(error)},
            ) from error
        checkpoint_id = self._checkpoints.snapshot(
            label=f"{name}:{tool_context.environment}",
            state=state,
            metadata={
                "tool": name,
                "environment": tool_context.environment,
                "host": tool_context.host,
                "risk_categories": sorted(risk_categories),
                "payload": dict(payload),
                "rollback_plan": rollback_plan,
            },
        )

        # One-time approval consumption happens only after a valid checkpoint
        # exists and immediately before execution.
        self._gate.consume(decision)
        if decision.approval_ref:
            self._events.publish(
                "approval.consumed",
                component="COMP-04",
                message=f"approval consumed for {name}",
                data={
                    "tool_name": name,
                    "request_id": decision.request_id,
                    "approval_ref": decision.approval_ref,
                },
            )
        self._events.publish(
            "tool.started",
            component="COMP-03",
            message=f"executing mutating tool: {name}",
            data={
                "tool": name,
                "checkpoint_id": checkpoint_id,
                "environment": tool_context.environment,
            },
        )

        apply_payload = {key: value for key, value in payload.items() if key != "dry_run"}
        max_retries = contract.max_retries or 0
        attempt = 0
        last_error: Exception | None = None
        result: ToolResult | None = None
        try:
            while attempt <= max_retries:
                attempt += 1
                try:
                    candidate = tool.apply(apply_payload)
                    if not isinstance(candidate, ToolResult):
                        raise ExecutionFailed(
                            f"tool {name} returned a non-ToolResult value"
                        )
                    if candidate.status != "success":
                        raise ExecutionFailed(
                            f"tool {name} returned status {candidate.status} from apply()"
                        )
                    validate_document(candidate.output, contract.output_schema)
                    tool.verify(apply_payload, candidate)
                    result = candidate
                    break
                except (ExecutionFailed, TimeoutExceeded) as error:
                    last_error = error
                    retryable = error.error_mode in contract.retryable_error_modes
                    if isinstance(error, TimeoutExceeded) or not retryable:
                        break
                    if attempt > max_retries:
                        break
                    backoff = min(
                        (contract.retry_base_seconds or 0)
                        * (2 ** (attempt - 1)),
                        contract.retry_cap_seconds or 0,
                    )
                    self._logger.warning(
                        "mutating tool attempt failed; retrying",
                        action=name,
                        attempt=attempt,
                        max_retries=max_retries,
                        backoff_seconds=backoff,
                        error_mode=error.error_mode,
                    )
                    self._events.publish(
                        "tool.retry",
                        component="COMP-03",
                        message=(
                            f"retrying {name} after {error.error_mode} "
                            f"(attempt {attempt}/{max_retries + 1})"
                        ),
                        data={
                            "tool": name,
                            "attempt": attempt,
                            "max_retries": max_retries,
                            "backoff_seconds": backoff,
                            "error_mode": error.error_mode,
                            "checkpoint_id": checkpoint_id,
                        },
                        level="WARN",
                    )
                    self._sleep(backoff)
                except VerificationFailed as error:
                    last_error = error
                    break
                except OpsPilotError as error:
                    last_error = error
                    break
        except Exception as error:  # defensive: never leak past audit/rollback
            last_error = error

        duration_ms = int((time.monotonic() - started) * 1000)
        if result is not None:
            result.checkpoint_id = checkpoint_id
            result.attempt_count = attempt
            result.duration_ms = duration_ms
            self._write_audit(
                action=name,
                inputs=payload,
                outcome="success",
                checkpoint_id=checkpoint_id,
                approval_ref=decision.approval_ref,
                evidence={
                    "dry_run": False,
                    "attempt_count": attempt,
                    "duration_ms": duration_ms,
                    "risk_categories": sorted(risk_categories),
                    **result.evidence,
                },
            )
            self._events.publish(
                "tool.completed",
                component="COMP-03",
                message=f"completed mutating tool: {name}",
                data={
                    "tool": name,
                    "status": "success",
                    "attempt_count": attempt,
                    "duration_ms": duration_ms,
                    "checkpoint_id": checkpoint_id,
                    "outcome": "success",
                },
            )
            return result

        assert last_error is not None
        rollback_ok = self._rollback(tool, state, name)
        evidence = {
            "dry_run": False,
            "attempt_count": attempt,
            "duration_ms": duration_ms,
            "risk_categories": sorted(risk_categories),
            "error_mode": getattr(last_error, "error_mode", "EXECUTION_FAILED"),
            "error": str(last_error),
            "rollback_succeeded": rollback_ok,
        }
        outcome = "rolled_back" if rollback_ok else "failed"
        self._write_audit(
            action=name,
            inputs=payload,
            outcome=outcome,
            checkpoint_id=checkpoint_id,
            approval_ref=decision.approval_ref,
            evidence=evidence,
        )
        self._logger.error(
            "mutating tool failed",
            action=name,
            error=str(last_error),
            rollback_succeeded=rollback_ok,
            checkpoint_id=checkpoint_id,
        )
        self._events.publish(
            "tool.failed",
            component="COMP-03",
            message=f"mutating tool failed: {name}",
            data={
                "tool": name,
                "status": outcome,
                "error": str(last_error),
                "error_mode": evidence["error_mode"],
                "attempt_count": attempt,
                "duration_ms": duration_ms,
                "checkpoint_id": checkpoint_id,
                "rollback_succeeded": rollback_ok,
            },
            level="ERROR",
        )
        if not rollback_ok:
            self._events.publish(
                "tool.rollback_failed",
                component="COMP-03",
                message=f"automatic rollback failed for {name}; host may be inconsistent",
                data={"tool": name, "checkpoint_id": checkpoint_id},
                level="CRITICAL",
            )
        return ToolResult(
            status="rolled_back" if rollback_ok else "failed",
            output={},
            evidence=evidence,
            checkpoint_id=checkpoint_id,
            attempt_count=attempt,
            duration_ms=duration_ms,
        )

    def _rollback(self, tool: MutatingTool, state: Mapping[str, Any], name: str) -> bool:
        try:
            tool.restore(state)
            return True
        except Exception as error:
            self._logger.critical(
                "automatic rollback failed; host may be inconsistent",
                action=name,
                error=str(error),
                error_type=type(error).__name__,
            )
            return False

    def _write_audit(
        self,
        *,
        action: str,
        inputs: Mapping[str, Any],
        outcome: str,
        checkpoint_id: str | None,
        approval_ref: str | None,
        evidence: dict[str, Any],
    ) -> None:
        if self._audit is None:
            return
        try:
            self._audit.append(
                action=action,
                inputs=inputs,
                outcome=outcome,
                checkpoint_id=checkpoint_id,
                approval_ref=approval_ref,
                evidence=evidence,
            )
        except Exception as error:
            self._logger.critical(
                "AUDIT WRITE FAILED after mutating action",
                action=action,
                outcome=outcome,
                error=str(error),
            )
            raise ExecutionFailed(
                "audit write failed; refusing to report mutating action as complete",
                details={"action": action, "outcome": outcome},
            ) from error
