"""Durable JSON Lines event journal (COMP-13 transport, NFR-014/NFR-015)."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from event_bus.model import Event


@dataclass(frozen=True)
class JournalPage:
    events: list[Event]
    offset: int
    identity: tuple[int, int]
    # Byte offset of each event's line start, parallel to ``events``.
    offsets: tuple[int, ...] = ()


class EventJournal:
    """Append-only journal with bounded rotation and byte-offset reads.

    Rotation is deliberately simple (``events.jsonl``, ``events.jsonl.1``, ...)
    because the UI only needs recent backfill; the audit log remains the
    authoritative long-term record.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = 20 * 1024 * 1024,
        max_files: int = 3,
    ) -> None:
        if max_bytes < 1024:
            raise ValueError("journal max_bytes must be at least 1024")
        if max_files < 1:
            raise ValueError("journal max_files must be at least 1")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.max_files = max_files
        self._lock = threading.Lock()

    def append(self, event: Event) -> None:
        line = json.dumps(event.to_dict(), ensure_ascii=True, separators=(",", ":"))
        with self._lock:
            self._rotate_if_needed(len(line) + 1)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
                handle.flush()

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if not self.path.exists():
            return
        current_bytes = self.path.stat().st_size
        # Rotate before the append that would cross the limit, not after it.
        # An empty journal is never rotated: a single line larger than the
        # budget is still written once rather than silently discarded.
        if current_bytes == 0 or current_bytes + incoming_bytes <= self.max_bytes:
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

    def identity(self) -> tuple[int, int]:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return (0, 0)
        return (getattr(stat, "st_ino", 0), stat.st_size)

    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except FileNotFoundError:
            return 0

    def read_from(self, offset: int = 0, *, limit: int = 2000) -> JournalPage:
        identity = self.identity()
        if offset < 0 or offset > identity[1]:
            offset = 0
        if not self.path.exists():
            return JournalPage(events=[], offset=0, identity=identity)

        with self.path.open("rb") as handle:
            handle.seek(offset)
            payload = handle.read()
        consumed = offset
        events: list[Event] = []
        offsets: list[int] = []
        lines = payload.splitlines(keepends=True)
        if lines and not lines[-1].endswith(b"\n"):
            # The final chunk is a line still being written; leave it for the
            # next read so the journal never surfaces a half event.
            lines = lines[:-1]
        for raw_line in lines:
            line_offset = consumed
            consumed += len(raw_line)
            stripped = raw_line.rstrip(b"\r\n")
            if not stripped:
                continue
            try:
                document = json.loads(stripped.decode("utf-8"))
                events.append(Event.from_mapping(document))
                offsets.append(line_offset)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                # A corrupt line is skipped rather than taking the UI down;
                # the source record still exists for forensic inspection.
                continue
            if len(events) >= limit:
                break
        return JournalPage(
            events=events,
            offset=consumed,
            identity=identity,
            offsets=tuple(offsets),
        )

    def read_all(self, *, limit: int | None = None) -> list[Event]:
        page = self.read_from(0, limit=limit or 10_000)
        return page.events

    def tail(self, *, limit: int = 500) -> list[Event]:
        events = self.read_all(limit=limit)
        return events[-limit:]

    def clear(self) -> None:
        with self._lock:
            if self.path.exists():
                os.remove(self.path)
