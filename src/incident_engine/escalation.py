"""Escalation notifier (COMP-07, FR-052).

Escalation carries enough diagnostic context for a human to act, but never a
secret. Redaction runs on the whole diagnostic payload before it is rendered.
"""

from __future__ import annotations

import json
import smtplib
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage as StdEmailMessage
from typing import Any, Protocol

from checkpoint_store.store import IncidentRecord
from common.errors import PreconditionFailed
from common.redaction import redact


@dataclass(frozen=True)
class NotificationConfig:
    recipients: tuple[str, ...]
    from_address: str
    smtp_host: str
    smtp_port: int
    use_starttls: bool
    credential_ref: str

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> NotificationConfig:
        notification = document.get("notification")
        if not isinstance(notification, Mapping):
            raise PreconditionFailed("alert-rules config has no notification block")
        recipients = tuple(str(item) for item in notification.get("recipients", []))
        if not recipients:
            raise PreconditionFailed("at least one escalation recipient is required")
        return cls(
            recipients=recipients,
            from_address=str(notification["from_address"]),
            smtp_host=str(notification["smtp_host"]),
            smtp_port=int(notification["smtp_port"]),
            use_starttls=bool(notification["use_starttls"]),
            credential_ref=str(notification["credential_ref"]),
        )


@dataclass(frozen=True)
class EmailMessage:
    recipients: tuple[str, ...]
    subject: str
    body: str


@dataclass(frozen=True)
class EscalationResult:
    sent: bool
    subject: str
    recipients: int
    error: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sent": self.sent,
            "subject": self.subject,
            "recipients": self.recipients,
            "error": self.error,
            "payload": self.payload,
        }


class EmailTransport(Protocol):
    def send(self, message: EmailMessage, *, password: str | None) -> None: ...


class SmtpTransport:
    def __init__(self, config: NotificationConfig, *, timeout_seconds: int = 30) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds

    def send(self, message: EmailMessage, *, password: str | None) -> None:
        email = StdEmailMessage()
        email["From"] = self.config.from_address
        email["To"] = ", ".join(message.recipients)
        email["Subject"] = message.subject
        email.set_content(message.body)
        context = ssl.create_default_context()
        with smtplib.SMTP(
            self.config.smtp_host, self.config.smtp_port, timeout=self.timeout_seconds
        ) as client:
            if self.config.use_starttls:
                client.starttls(context=context)
            if password:
                client.login(self.config.from_address, password)
            client.send_message(email)


class EscalationNotifier:
    def __init__(
        self,
        config: NotificationConfig,
        *,
        transport: EmailTransport | None = None,
        credential_provider: Callable[[str], str | None] | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or SmtpTransport(config)
        self._credential_provider = credential_provider

    def render(
        self, incident: IncidentRecord, diagnostics: Mapping[str, Any]
    ) -> EmailMessage:
        payload = {
            "incident_id": incident.id,
            "app": incident.app,
            "service": incident.service,
            "status": incident.status,
            "attempts": incident.attempts,
            "detected_at": incident.detected_at.isoformat(),
            "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
            "summary": incident.summary,
            "details": redact(dict(incident.details)),
            "diagnostics": redact(dict(diagnostics)),
        }
        subject = (
            f"[OpsPilot][{incident.status}] {incident.app}/{incident.service} "
            f"({incident.id})"
        )
        body = (
            "OpsPilot could not remediate an incident and is escalating it for a human.\n\n"
            "Diagnostic context follows (secrets redacted):\n\n"
            + json.dumps(payload, indent=2, sort_keys=True, default=str)
            + "\n"
        )
        return EmailMessage(
            recipients=self._config.recipients, subject=subject, body=body
        )

    @staticmethod
    def payload_from_message(message: EmailMessage) -> dict[str, Any]:
        """Extract the JSON diagnostic block from a rendered message."""
        marker = "Diagnostic context follows (secrets redacted):"
        _, _, remainder = message.body.partition(marker)
        document: Any = json.loads(remainder.strip())
        if not isinstance(document, dict):
            raise PreconditionFailed("escalation payload was not a JSON object")
        return dict(document)

    def escalate(
        self, incident: IncidentRecord, diagnostics: Mapping[str, Any]
    ) -> EscalationResult:
        message = self.render(incident, diagnostics)
        password = None
        if self._credential_provider is not None:
            password = self._credential_provider(self._config.credential_ref)
        try:
            self._transport.send(message, password=password)
        except Exception as error:
            return EscalationResult(
                sent=False,
                subject=message.subject,
                recipients=len(message.recipients),
                error=f"{type(error).__name__}: {error}",
                payload=self.payload_from_message(message),
            )
        return EscalationResult(
            sent=True,
            subject=message.subject,
            recipients=len(message.recipients),
            payload=self.payload_from_message(message),
        )
