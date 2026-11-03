"""Bounded remediation executor (COMP-07, FR-051/NFR-004)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from common.clock import Clock, SystemClock
from common.contracts import ToolContext
from common.errors import ApprovalRequired
from common.log import StructuredLogger
from deployment_engine.health import HealthChecker
from incident_engine.health import HealthTargetSpec
from tools_mutating.registry import MutatingToolRegistry


@dataclass(frozen=True)
class RemediationOutcome:
    status: str
    attempts: int
    evidence: dict[str, Any] = field(default_factory=dict)
    approval_request_id: str | None = None

    @property
    def resolved(self) -> bool:
        return self.status == "resolved"

    @property
    def escalated(self) -> bool:
        return self.status == "escalated"


class RemediationExecutor:
    def __init__(
        self,
        registry: MutatingToolRegistry,
        health_checker: HealthChecker,
        *,
        clock: Clock | None = None,
        max_attempts: int = 2,
        backoff_seconds: float = 5.0,
        logger: StructuredLogger | None = None,
    ) -> None:
        if max_attempts < 1 or max_attempts > 10:
            raise ValueError("max_attempts must be between 1 and 10")
        self._registry = registry
        self._health = health_checker
        self._clock = clock or SystemClock()
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._logger = logger or StructuredLogger("COMP-07")

    def remediate(
        self, target: HealthTargetSpec, *, context: ToolContext | None = None
    ) -> RemediationOutcome:
        evidence: dict[str, Any] = {"service": target.service, "attempts": []}
        for attempt in range(1, self._max_attempts + 1):
            try:
                result = self._registry.execute(
                    "restart_windows_service",
                    {"service_name": target.service},
                    context=context or ToolContext(),
                    rollback_plan=f"service restart is reversible; incident remediation for {target.service}",
                )
            except ApprovalRequired as error:
                evidence["approval_request_id"] = error.request_id
                return RemediationOutcome(
                    status="escalated",
                    attempts=attempt,
                    evidence=evidence,
                    approval_request_id=error.request_id,
                )
            attempt_record = {
                "attempt": attempt,
                "result": result.to_dict(),
            }
            evidence["attempts"].append(attempt_record)
            if result.status == "success":
                healthy, health_error = self._health.check(target.health, port=target.port)
                attempt_record["health_after"] = {"healthy": healthy, "error": health_error}
                if healthy:
                    return RemediationOutcome(
                        status="resolved", attempts=attempt, evidence=evidence
                    )
            if attempt < self._max_attempts:
                self._clock.sleep(self._backoff)
        self._logger.error(
            "remediation attempts exhausted",
            service=target.service,
            attempts=self._max_attempts,
        )
        return RemediationOutcome(
            status="failed", attempts=self._max_attempts, evidence=evidence
        )
