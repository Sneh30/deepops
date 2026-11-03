"""Canonical dashboard event envelope (ENGINEERING_REFERENCE.md §4.4/§7)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from common.ids import new_id
from common.redaction import redact, redact_text

LEVELS = {"DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"}

# Component IDs used by the top status strip (UI_SPEC.md §3).
COMPONENT_TILES: tuple[tuple[str, str], ...] = (
    ("COMP-09", "Provisioning"),
    ("COMP-08", "Deploy"),
    ("COMP-10", "Proxy/TLS"),
    ("COMP-06", "Metrics"),
    ("COMP-07", "Incident Engine"),
    ("COMP-11", "Secrets"),
    ("COMP-12", "Audit"),
    ("COMP-04", "Approval Gate"),
)


def utc_timestamp(value: datetime | None = None) -> str:
    moment = value or datetime.now(UTC)
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Event:
    id: str
    timestamp: str
    type: str
    component: str
    level: str
    message: str
    data: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        event_type: str,
        component: str,
        message: str,
        data: Mapping[str, Any] | None = None,
        level: str = "INFO",
        timestamp: datetime | None = None,
        identifier: str | None = None,
    ) -> Event:
        if not event_type or not component:
            raise ValueError("event type and component are required")
        normalized_level = level.upper()
        if normalized_level not in LEVELS:
            raise ValueError(f"unknown event level: {level!r}")
        return cls(
            id=identifier or new_id("evt"),
            timestamp=utc_timestamp(timestamp),
            type=event_type,
            component=component,
            level=normalized_level,
            message=redact_text(message),
            data=redact(dict(data or {})),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> Event:
        required = {"id", "timestamp", "type", "component", "level", "message", "data"}
        missing = sorted(required - set(value))
        if missing:
            raise ValueError(f"event missing required fields: {', '.join(missing)}")
        level = str(value["level"]).upper()
        if level not in LEVELS:
            raise ValueError(f"unknown event level: {level!r}")
        data = value["data"]
        if not isinstance(data, Mapping):
            raise ValueError("event data must be an object")
        timestamp = str(value["timestamp"])
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return cls(
            id=str(value["id"]),
            timestamp=timestamp,
            type=str(value["type"]),
            component=str(value["component"]),
            level=level,
            message=str(value["message"]),
            data=dict(data),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "type": self.type,
            "component": self.component,
            "level": self.level,
            "message": self.message,
            "data": dict(self.data),
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]
