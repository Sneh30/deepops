"""Structured log aggregation and query (COMP-06, FR-041).

Reads the JSON Lines format produced by ``common.log.StructuredLogger``. The
aggregator never rewrites source logs; it validates and queries them.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from common.config import canonical_json
from common.errors import PreconditionFailed

_LEVELS = {"DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"}


@dataclass(frozen=True)
class LogEvent:
    timestamp: datetime
    level: str
    component: str
    message: str
    context: dict[str, Any]
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        document = {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level,
            "component": self.component,
            "message": self.message,
            "context": self.context,
        }
        if self.source is not None:
            document["source"] = self.source
        return document


@dataclass(frozen=True)
class LogSummary:
    total: int
    by_level: dict[str, int]
    by_component: dict[str, int]
    error_count: int
    critical_count: int
    first_timestamp: datetime | None
    last_timestamp: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "by_level": self.by_level,
            "by_component": self.by_component,
            "error_count": self.error_count,
            "critical_count": self.critical_count,
            "first_timestamp": self.first_timestamp.isoformat()
            if self.first_timestamp
            else None,
            "last_timestamp": self.last_timestamp.isoformat()
            if self.last_timestamp
            else None,
        }


class LogAggregator:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def iter_events(self, paths: Iterable[str | Path]) -> Iterable[LogEvent]:
        for path_value in paths:
            path = Path(path_value)
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        document = json.loads(line)
                    except json.JSONDecodeError as error:
                        self.errors.append(f"{path}:{line_number}: invalid JSON: {error}")
                        continue
                    event = self._parse(document, path, line_number)
                    if event is not None:
                        yield event

    def _parse(
        self, document: Mapping[str, Any], path: Path, line_number: int
    ) -> LogEvent | None:
        required = {"timestamp", "level", "component", "message", "context"}
        if not required <= set(document):
            missing = sorted(required - set(document))
            self.errors.append(f"{path}:{line_number}: missing fields {missing}")
            return None
        level = str(document["level"])
        if level not in _LEVELS:
            self.errors.append(f"{path}:{line_number}: unknown level {level!r}")
            return None
        try:
            timestamp = datetime.fromisoformat(
                str(document["timestamp"]).replace("Z", "+00:00")
            )
        except ValueError:
            self.errors.append(f"{path}:{line_number}: invalid timestamp")
            return None
        context = document["context"]
        if not isinstance(context, dict):
            self.errors.append(f"{path}:{line_number}: context is not an object")
            return None
        return LogEvent(
            timestamp=timestamp,
            level=level,
            component=str(document["component"]),
            message=str(document["message"]),
            context=dict(context),
            source=str(path),
        )

    def query(
        self,
        paths: Sequence[str | Path],
        *,
        level: str | None = None,
        component: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        contains: str | None = None,
        limit: int = 200,
    ) -> list[LogEvent]:
        if level is not None and level not in _LEVELS:
            raise PreconditionFailed(f"unknown log level: {level}")
        matches: list[LogEvent] = []
        for event in self.iter_events(paths):
            if level is not None and event.level != level:
                continue
            if component is not None and event.component != component:
                continue
            if since is not None and event.timestamp < since:
                continue
            if until is not None and event.timestamp > until:
                continue
            if contains is not None and contains.lower() not in event.message.lower():
                continue
            matches.append(event)
        matches.sort(key=lambda event: event.timestamp)
        return matches[-limit:]

    def summarize(
        self,
        paths: Sequence[str | Path],
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> LogSummary:
        levels: Counter[str] = Counter()
        components: Counter[str] = Counter()
        first: datetime | None = None
        last: datetime | None = None
        total = 0
        for event in self.iter_events(paths):
            if since is not None and event.timestamp < since:
                continue
            if until is not None and event.timestamp > until:
                continue
            total += 1
            levels[event.level] += 1
            components[event.component] += 1
            first = event.timestamp if first is None else min(first, event.timestamp)
            last = event.timestamp if last is None else max(last, event.timestamp)
        return LogSummary(
            total=total,
            by_level=dict(sorted(levels.items())),
            by_component=dict(sorted(components.items())),
            error_count=levels.get("ERROR", 0),
            critical_count=levels.get("CRITICAL", 0),
            first_timestamp=first,
            last_timestamp=last,
        )


def event_digest(event: LogEvent) -> str:
    from hashlib import sha256

    return sha256(canonical_json(event.to_dict()).encode("utf-8")).hexdigest()
