"""Append-only structured audit log (COMP-12, FR-093, NFR-007).

Every mutating agent action produces exactly one JSON Lines record with the
seven required fields from ENGINEERING_REFERENCE.md §4.3. The writer redacts
inputs/evidence at the call site so secrets cannot enter the audit trail.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common.redaction import redact
from event_bus.publisher import EventPublisher, NullEventPublisher

REQUIRED_FIELDS = (
    "timestamp",
    "action",
    "inputs",
    "outcome",
    "checkpoint_id",
    "approval_ref",
    "evidence",
)
OUTCOMES = {"success", "failed", "rolled_back"}


@dataclass(frozen=True)
class AuditEntry:
    timestamp: str
    action: str
    inputs: dict[str, Any]
    outcome: str
    checkpoint_id: str | None
    approval_ref: str | None
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "action": self.action,
            "inputs": self.inputs,
            "outcome": self.outcome,
            "checkpoint_id": self.checkpoint_id,
            "approval_ref": self.approval_ref,
            "evidence": self.evidence,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> AuditEntry:
        missing = [field for field in REQUIRED_FIELDS if field not in value]
        if missing:
            raise ValueError(f"audit entry missing required fields: {', '.join(missing)}")
        outcome = value["outcome"]
        if outcome not in OUTCOMES:
            raise ValueError(f"invalid audit outcome: {outcome}")
        timestamp = value["timestamp"]
        if not isinstance(timestamp, str):
            raise ValueError("audit timestamp must be an ISO-8601 string")
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if not isinstance(value["action"], str) or not value["action"]:
            raise ValueError("audit action must be a non-empty string")
        for name in ("inputs", "evidence"):
            if not isinstance(value[name], dict):
                raise ValueError(f"audit {name} must be an object")
        for name in ("checkpoint_id", "approval_ref"):
            if value[name] is not None and not isinstance(value[name], str):
                raise ValueError(f"audit {name} must be a string or null")
        return cls(
            timestamp=timestamp,
            action=value["action"],
            inputs=dict(value["inputs"]),
            outcome=outcome,
            checkpoint_id=value["checkpoint_id"],
            approval_ref=value["approval_ref"],
            evidence=dict(value["evidence"]),
        )


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class AuditLog:
    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = 10 * 1024 * 1024,
        max_files: int = 5,
        events: EventPublisher | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.max_files = max_files
        self._events = events or NullEventPublisher()

    def append(
        self,
        *,
        action: str,
        inputs: Mapping[str, Any],
        outcome: str,
        checkpoint_id: str | None,
        approval_ref: str | None,
        evidence: Mapping[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> AuditEntry:
        entry = AuditEntry.from_mapping(
            {
                "timestamp": timestamp or _utc_now_iso(),
                "action": action,
                "inputs": redact(dict(inputs)),
                "outcome": outcome,
                "checkpoint_id": checkpoint_id,
                "approval_ref": approval_ref,
                "evidence": redact(dict(evidence or {})),
            }
        )
        self._rotate_if_needed()
        line = json.dumps(entry.to_dict(), ensure_ascii=True, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._events.publish(
            "audit.appended",
            component="COMP-12",
            message=f"audit: {action} -> {outcome}",
            data={
                "action": action,
                "outcome": outcome,
                "checkpoint_id": checkpoint_id,
                "approval_ref": approval_ref,
            },
            level="ERROR" if outcome == "failed" else "INFO",
        )
        return entry

    def _rotate_if_needed(self) -> None:
        if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
            return
        if self.max_files <= 1:
            self.path.unlink()
            return
        oldest = self.path.with_name(f"{self.path.name}.{self.max_files - 1}")
        if oldest.exists():
            oldest.unlink()
        for index in range(self.max_files - 2, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            if source.exists():
                source.rename(self.path.with_name(f"{self.path.name}.{index + 1}"))
        self.path.rename(self.path.with_name(f"{self.path.name}.1"))

    def _files_oldest_first(self) -> Iterator[Path]:
        for index in range(self.max_files - 1, 0, -1):
            candidate = self.path.with_name(f"{self.path.name}.{index}")
            if candidate.exists():
                yield candidate
        if self.path.exists():
            yield self.path

    def read_all(self) -> list[AuditEntry]:
        entries: list[AuditEntry] = []
        for file_path in self._files_oldest_first():
            entries.extend(self._read_file(file_path))
        return entries

    def _read_file(self, path: Path) -> Iterable[AuditEntry]:
        with path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                    yield AuditEntry.from_mapping(payload)
                except (json.JSONDecodeError, ValueError) as error:
                    raise ValueError(f"invalid audit line {path}:{number}: {error}") from error

    def query(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        action: str | None = None,
        outcome: str | None = None,
    ) -> list[AuditEntry]:
        selected = []
        for entry in self.read_all():
            timestamp = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
            if start is not None and timestamp < start:
                continue
            if end is not None and timestamp > end:
                continue
            if action is not None and entry.action != action:
                continue
            if outcome is not None and entry.outcome != outcome:
                continue
            selected.append(entry)
        return selected

    def count(self) -> int:
        return sum(1 for _ in self._files_oldest_first() for _ in self._read_file(_))
