"""COMP-07 incident engine: polling, remediation, escalation, alerting, drift."""

from incident_engine.alerts import Alert, AlertEvaluator, AlertRule, MetricSnapshot
from incident_engine.drift import (
    DriftDetector,
    DriftFinding,
    DriftReport,
    LiveStateProvider,
    PowerShellBaselineProvider,
)
from incident_engine.engine import IncidentEngine
from incident_engine.escalation import (
    EmailMessage,
    EscalationNotifier,
    EscalationResult,
    NotificationConfig,
    SmtpTransport,
)
from incident_engine.health import HealthPoller, HealthTargetSpec, PollResult
from incident_engine.remediation import RemediationExecutor, RemediationOutcome

__all__ = [
    "Alert",
    "AlertEvaluator",
    "AlertRule",
    "DriftDetector",
    "DriftFinding",
    "DriftReport",
    "EmailMessage",
    "EscalationNotifier",
    "EscalationResult",
    "HealthPoller",
    "HealthTargetSpec",
    "IncidentEngine",
    "LiveStateProvider",
    "MetricSnapshot",
    "NotificationConfig",
    "PollResult",
    "PowerShellBaselineProvider",
    "RemediationExecutor",
    "RemediationOutcome",
    "SmtpTransport",
]
