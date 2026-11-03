"""Incident lifecycle orchestration (COMP-07, FR-050-FR-052).

State machine from ENGINEERING_REFERENCE.md §7:
Detected -> Remediation-Attempted -> Resolved | Escalated -> Human-Resolved.
Every transition is written to the audit log.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from audit_log.writer import AuditLog
from checkpoint_store.store import CheckpointStore, IncidentRecord
from common.ids import incident_id
from event_bus.publisher import EventPublisher, NullEventPublisher
from incident_engine.escalation import EscalationNotifier, EscalationResult
from incident_engine.health import HealthPoller, PollResult
from incident_engine.remediation import RemediationExecutor


class IncidentEngine:
    def __init__(
        self,
        store: CheckpointStore,
        poller: HealthPoller,
        remediator: RemediationExecutor,
        *,
        notifier: EscalationNotifier | None = None,
        audit: AuditLog | None = None,
        id_factory: Callable[[], str] = incident_id,
        events: EventPublisher | None = None,
    ) -> None:
        self._store = store
        self._poller = poller
        self._remediator = remediator
        self._notifier = notifier
        self._audit = audit
        self._id_factory = id_factory
        self._events = events or NullEventPublisher()

    def poll_once(self) -> list[IncidentRecord]:
        incidents: list[IncidentRecord] = []
        for result in self._poller.poll_once():
            if result.triggered:
                incidents.append(self.handle_failure(result))
        return incidents

    def handle_failure(self, result: PollResult) -> IncidentRecord:
        target = result.target
        existing = self._open_incident(target.service)
        if existing is None:
            incident = self._store.create_incident(
                app=target.app,
                service=target.service,
                summary=f"health check failed: {result.error or 'unhealthy'}",
                details={
                    "target": target.name,
                    "consecutive_failures": result.consecutive_failures,
                    "error": result.error,
                },
                incident_id=self._id_factory(),
            )
            self._transition(
                incident, "Detected", "Remediation-Attempted", "remediation started"
            )
            self._events.publish(
                "incident.opened",
                component="COMP-07",
                message=f"incident opened for {target.service}: {incident.summary}",
                data={
                    "incident_id": incident.id,
                    "app": target.app,
                    "service": target.service,
                    "summary": incident.summary,
                },
                level="ERROR",
            )
        else:
            incident = existing

        outcome = self._remediator.remediate(target)
        attempts = incident.attempts + outcome.attempts
        self._events.publish(
            "incident.remediated",
            component="COMP-07",
            message=(
                f"remediation {'succeeded' if outcome.resolved else 'did not resolve'} "
                f"for {target.service}"
            ),
            data={
                "incident_id": incident.id,
                "app": target.app,
                "service": target.service,
                "attempt_count": outcome.attempts,
                "resolved": outcome.resolved,
                "escalated": outcome.escalated,
                "evidence": outcome.evidence,
            },
            level="INFO" if outcome.resolved else "WARN",
        )
        if outcome.resolved:
            resolved = self._store.update_incident(
                incident.id,
                status="Resolved",
                attempts=attempts,
                details={
                    **incident.details,
                    "remediation": outcome.evidence,
                    "resolved_by": "automatic",
                },
                resolved=True,
            )
            self._transition(resolved, "Remediation-Attempted", "Resolved", "remediation succeeded")
            self._poller.reset(target.name)
            self._events.publish(
                "incident.resolved",
                component="COMP-07",
                message=f"incident resolved automatically: {target.service}",
                data={
                    "incident_id": resolved.id,
                    "app": target.app,
                    "service": target.service,
                    "attempt_count": attempts,
                    "resolved": True,
                },
            )
            return resolved

        if outcome.escalated:
            status = "Escalated"
            summary = "approval required before remediation can continue"
        else:
            status = "Escalated"
            summary = "automatic remediation exhausted; human intervention required"
        escalated = self._store.update_incident(
            incident.id,
            status=status,
            attempts=attempts,
            summary=summary,
            details={
                **incident.details,
                "remediation": outcome.evidence,
                "approval_request_id": outcome.approval_request_id,
            },
        )
        self._transition(escalated, "Remediation-Attempted", "Escalated", summary)
        self._events.publish(
            "incident.escalated",
            component="COMP-07",
            message=f"incident escalated for {target.service}: {summary}",
            data={
                "incident_id": escalated.id,
                "app": target.app,
                "service": target.service,
                "attempt_count": attempts,
                "approval_request_id": outcome.approval_request_id,
            },
            level="ERROR",
        )
        if self._notifier is not None:
            escalation = self._notifier.escalate(
                escalated,
                {
                    "health": result.details,
                    "remediation": outcome.evidence,
                    "approval_request_id": outcome.approval_request_id,
                },
            )
            self._record_escalation(escalated, escalation)
        return escalated

    def resolve_human(self, incident_id_value: str, *, note: str) -> IncidentRecord:
        incident = self._store.get_incident(incident_id_value)
        resolved = self._store.update_incident(
            incident.id,
            status="Human-Resolved",
            details={**incident.details, "human_resolution": note},
            resolved=True,
        )
        self._transition(resolved, incident.status, "Human-Resolved", note)
        self._events.publish(
            "incident.resolved",
            component="COMP-07",
            message=f"incident human-resolved: {incident.service}",
            data={
                "incident_id": resolved.id,
                "app": incident.app,
                "service": incident.service,
                "resolved": True,
                "resolved_by": "human",
            },
        )
        return resolved

    def _open_incident(self, service: str) -> IncidentRecord | None:
        for incident in reversed(self._store.list_incidents()):
            if incident.service == service and incident.status not in {
                "Resolved",
                "Human-Resolved",
            }:
                return incident
        return None

    def _transition(
        self, incident: IncidentRecord, previous: str, current: str, reason: str
    ) -> None:
        if self._audit is None:
            return
        self._audit.append(
            action="incident_transition",
            inputs={
                "incident_id": incident.id,
                "from": previous,
                "to": current,
                "reason": reason,
            },
            outcome="success",
            checkpoint_id=None,
            approval_ref=incident.details.get("approval_request_id"),
            evidence={"attempts": incident.attempts, "service": incident.service},
        )

    def _record_escalation(
        self, incident: IncidentRecord, escalation: EscalationResult
    ) -> None:
        if self._audit is None:
            return
        self._audit.append(
            action="incident_escalation",
            inputs={"incident_id": incident.id, "channel": "email"},
            outcome="success" if escalation.sent else "failed",
            checkpoint_id=None,
            approval_ref=None,
            evidence={
                "subject": escalation.subject,
                "recipients": escalation.recipients,
                "error": escalation.error,
                "payload_sha256": _payload_digest(escalation.payload),
            },
        )


def _payload_digest(payload: dict[str, Any]) -> str:
    import hashlib

    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
