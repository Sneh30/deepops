"""Internal event bus and durable event journal (COMP-13, IMPL-701).

Producers publish structured events to the journal; the dashboard UI service
tails the same journal and fans events out to browser clients through an
in-process asyncio pub/sub. The journal is the transport that keeps the UI
service independent of the Control Plane (NFR-014) while ensuring every UI
event has a real upstream record (NFR-015).
"""

from event_bus.bus import EventBus, Subscription
from event_bus.follower import JournalFollower
from event_bus.journal import EventJournal
from event_bus.model import Event
from event_bus.publisher import (
    EventPublisher,
    JournalEventPublisher,
    NullEventPublisher,
)

__all__ = [
    "Event",
    "EventBus",
    "EventJournal",
    "EventPublisher",
    "JournalEventPublisher",
    "JournalFollower",
    "NullEventPublisher",
    "Subscription",
]
