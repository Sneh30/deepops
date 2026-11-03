"""Durable dashboard state snapshot (NFR-014 restart backfill)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common.config import atomic_write_json


@dataclass(frozen=True)
class DashboardSnapshot:
    version: int
    saved_at: str
    journal_offset: int
    state: Mapping[str, Any]


class SnapshotStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> DashboardSnapshot | None:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        if not isinstance(document, dict) or document.get("version") != 1:
            return None
        offset = document.get("journal_offset")
        state = document.get("state")
        if not isinstance(offset, int) or offset < 0 or not isinstance(state, Mapping):
            return None
        return DashboardSnapshot(
            version=1,
            saved_at=str(document.get("saved_at") or ""),
            journal_offset=offset,
            state=state,
        )

    def save(self, *, journal_offset: int, state: Mapping[str, Any]) -> None:
        if journal_offset < 0:
            raise ValueError("journal_offset must not be negative")
        atomic_write_json(
            self.path,
            {
                "version": 1,
                "saved_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "journal_offset": journal_offset,
                "state": dict(state),
            },
        )

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
