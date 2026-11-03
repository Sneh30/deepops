"""Producer-side event publisher (COMP-01/03/04/05/06/07/12 -> COMP-13)."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from common.clock import Clock, SystemClock
from event_bus.journal import EventJournal
from event_bus.model import Event


class EventPublisher(Protocol):
    def publish(
        self,
        event_type: str,
        *,
        component: str,
        message: str,
        data: Mapping[str, Any] | None = None,
        level: str = "INFO",
        timestamp: datetime | None = None,
    ) -> Event | None: ...


class NullEventPublisher:
    """No-op publisher used by tests and components without a journal."""

    def publish(
        self,
        event_type: str,
        *,
        component: str,
        message: str,
        data: Mapping[str, Any] | None = None,
        level: str = "INFO",
        timestamp: datetime | None = None,
    ) -> Event | None:
        return None


class JournalEventPublisher:
    """Writes events to the journal; never lets a UI failure break the agent.

    NFR-014 requires dashboard failure to leave the Control Plane unaffected.
    A journal write failure is therefore reported on stderr and swallowed;
    the component's own log/audit path remains authoritative.
    """

    def __init__(
        self,
        journal: EventJournal,
        *,
        clock: Clock | None = None,
    ) -> None:
        self.journal = journal
        self._clock = clock or SystemClock()

    def publish(
        self,
        event_type: str,
        *,
        component: str,
        message: str,
        data: Mapping[str, Any] | None = None,
        level: str = "INFO",
        timestamp: datetime | None = None,
    ) -> Event | None:
        event = Event.create(
            event_type=event_type,
            component=component,
            message=message,
            data=data,
            level=level,
            timestamp=timestamp or self._clock.now(),
        )
        try:
            self.journal.append(event)
        except Exception as error:  # pragma: no cover - defensive I/O path
            print(
                f"event journal write failed ({type(error).__name__}): {error}",
                file=sys.stderr,
            )
            return None
        return event
