"""Structured log + audit feed for the Observability tab (UI_SPEC.md §5.2)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from observability.logs import LogAggregator

_LEVELS = {"DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"}


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _audit_entries(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines: list[str] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    lines.append(line)
    except OSError:
        return []
    entries: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            document = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(document, dict):
            continue
        timestamp = _parse_timestamp(str(document.get("timestamp", "")))
        if timestamp is None:
            continue
        outcome = str(document.get("outcome", "unknown"))
        entries.append(
            {
                "timestamp": timestamp.isoformat(),
                "level": "ERROR" if outcome == "failed" else "INFO",
                "component": "COMP-12",
                "message": f"audit: {document.get('action', 'unknown')} -> {outcome}",
                "context": {
                    "action": document.get("action"),
                    "outcome": outcome,
                    "checkpoint_id": document.get("checkpoint_id"),
                    "approval_ref": document.get("approval_ref"),
                    "source": "audit",
                },
            }
        )
    return entries


class LogFeed:
    def __init__(
        self,
        log_dir: str | Path,
        *,
        glob: str = "*.jsonl",
        audit_path: str | Path | None = None,
        limit: int = 500,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.glob = glob
        self.audit_path = Path(audit_path) if audit_path else None
        self.limit = limit

    def _paths(self) -> list[Path]:
        if not self.log_dir.exists():
            return []
        audit_path = self.audit_path.resolve() if self.audit_path else None
        return sorted(
            path
            for path in self.log_dir.glob(self.glob)
            if path.is_file()
            and path.name != "events.jsonl"
            and (audit_path is None or path.resolve() != audit_path)
        )

    def query(
        self,
        *,
        level: str | None = None,
        component: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        selected_limit = min(max(1, limit or self.limit), self.limit)
        if level is not None and level not in _LEVELS:
            raise ValueError(f"unknown log level: {level}")
        aggregator = LogAggregator()
        events = [
            event.to_dict()
            for event in aggregator.query(
                self._paths(),
                level=level,
                component=component,
                limit=selected_limit,
            )
        ]
        if self.audit_path is not None:
            audit = _audit_entries(self.audit_path, limit=selected_limit)
            events.extend(
                entry
                for entry in audit
                if (level is None or entry["level"] == level)
                and (component is None or entry["component"] == component)
            )
        events.sort(key=lambda item: str(item["timestamp"]))
        return {
            "events": events[-selected_limit:],
            "errors": aggregator.errors[-10:],
            "path_count": len(self._paths()),
        }

    def summarize(self) -> dict[str, Any]:
        aggregator = LogAggregator()
        summary = aggregator.summarize(self._paths())
        audit = _audit_entries(self.audit_path, limit=self.limit) if self.audit_path else []
        return {
            **summary.to_dict(),
            "audit_entries": len(audit),
            "errors": aggregator.errors[-10:],
        }


def iter_audit(path: str | Path) -> Iterable[dict[str, Any]]:
    """Yield parsed audit documents without importing the writer."""
    target = Path(path)
    if not target.exists():
        return
    with target.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                document = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(document, dict):
                yield document
