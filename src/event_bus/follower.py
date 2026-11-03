"""Tail the durable event journal inside the dashboard process (IMPL-703)."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable

from event_bus.journal import EventJournal
from event_bus.model import Event

EventHandler = Callable[[Event], None]


class JournalFollower:
    """Tail a growing journal, detecting rotation and partial writes.

    The follower deliberately keeps only a bounded in-memory duplicate filter:
    the journal itself is the durable record, and the dashboard state snapshot
    supplies long-horizon state across restarts.
    """

    def __init__(
        self,
        journal: EventJournal,
        on_event: EventHandler,
        *,
        poll_interval_seconds: float = 0.05,
        batch_size: int = 500,
        dedupe_limit: int = 10_000,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll interval must be positive")
        if batch_size < 1:
            raise ValueError("batch size must be positive")
        self._journal = journal
        self._on_event = on_event
        self._poll = poll_interval_seconds
        self._batch = batch_size
        self._offset = 0
        self._identity: tuple[int, int] | None = None
        self._seen: deque[str] = deque(maxlen=dedupe_limit)
        self._seen_set: set[str] = set()

    @property
    def offset(self) -> int:
        return self._offset

    @property
    def identity(self) -> tuple[int, int] | None:
        return self._identity

    def set_offset(self, offset: int) -> None:
        if offset < 0:
            raise ValueError("offset must not be negative")
        self._offset = offset
        self._identity = self._journal.identity()

    def prime(self) -> int:
        """Start at the current end of the journal (live-only mode)."""
        identity = self._journal.identity()
        self._identity = identity
        self._offset = identity[1]
        return self._offset

    def poll(self) -> int:
        """Apply one batch of journal events; returns the number delivered.

        Synchronous by design so tests can drive the exact production reducer
        path without a live event loop.
        """
        try:
            page = self._journal.read_from(self._offset, limit=self._batch)
        except OSError:
            return 0

        if self._rotated(page.identity):
            # The journal was rotated, truncated, or replaced. Replay the
            # available file from the beginning so recent events are not
            # silently dropped.
            self._offset = 0
            self._seen.clear()
            self._seen_set.clear()
            try:
                page = self._journal.read_from(0, limit=self._batch)
            except OSError:
                return 0
        self._identity = page.identity

        delivered = 0
        for event in page.events:
            if event.id in self._seen_set:
                continue
            self._remember(event.id)
            self._on_event(event)
            delivered += 1
        self._offset = page.offset
        return delivered

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            delivered = self.poll()
            if delivered:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._poll)
            except TimeoutError:
                continue

    def _rotated(self, identity: tuple[int, int]) -> bool:
        if self._identity is None:
            return False
        previous_inode, previous_size = self._identity
        current_inode, current_size = identity
        # st_ino can be 0 on some Windows filesystems; size shrink is then the
        # reliable rotation signal.
        if previous_inode and current_inode and previous_inode != current_inode:
            return True
        return current_size < self._offset or current_size < previous_size

    def _remember(self, event_id: str) -> None:
        if len(self._seen) == self._seen.maxlen:
            oldest = self._seen.popleft()
            self._seen_set.discard(oldest)
        self._seen.append(event_id)
        self._seen_set.add(event_id)

    def seen(self, event_id: str) -> bool:
        return event_id in self._seen_set
