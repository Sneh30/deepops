"""Structured JSON Lines logging (ENGINEERING_REFERENCE.md §7).

Every event carries: timestamp, level, component, message, context.
Human-readable formatting is deliberately absent; agents and log pipelines are
the primary consumers.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

from common.redaction import redact, redact_text

_LEVELS = {
    "DEBUG": 10,
    "INFO": 20,
    "WARN": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class StructuredLogger:
    """Writes one JSON object per line to a text stream."""

    def __init__(
        self,
        component: str,
        *,
        stream: TextIO | None = None,
        minimum_level: str = "INFO",
    ) -> None:
        if minimum_level not in _LEVELS:
            raise ValueError(f"unknown log level: {minimum_level}")
        if not component:
            raise ValueError("component is required")
        self.component = component
        self.stream = stream if stream is not None else sys.stderr
        self.minimum_level = minimum_level

    def _emit(self, level: str, message: str, context: dict[str, Any] | None = None) -> None:
        if _LEVELS[level] < _LEVELS[self.minimum_level]:
            return
        event = {
            "timestamp": _timestamp(),
            "level": level,
            "component": self.component,
            "message": redact_text(message),
            "context": redact(context or {}),
        }
        self.stream.write(json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n")
        self.stream.flush()

    def debug(self, message: str, **context: Any) -> None:
        self._emit("DEBUG", message, context)

    def info(self, message: str, **context: Any) -> None:
        self._emit("INFO", message, context)

    def warning(self, message: str, **context: Any) -> None:
        self._emit("WARN", message, context)

    def error(self, message: str, **context: Any) -> None:
        self._emit("ERROR", message, context)

    def critical(self, message: str, **context: Any) -> None:
        self._emit("CRITICAL", message, context)

    def exception(
        self, message: str, error: BaseException, **context: Any
    ) -> None:
        self._emit(
            "ERROR",
            message,
            {
                **context,
                "error_type": type(error).__name__,
                "error": str(error),
                "error_mode": getattr(error, "error_mode", None),
            },
        )
