from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from event_bus.bus import EventBus
from event_bus.follower import JournalFollower
from event_bus.journal import EventJournal
from event_bus.model import Event
from event_bus.publisher import JournalEventPublisher, NullEventPublisher

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def make_event(index: int = 0, *, event_type: str = "tool.started") -> Event:
    return Event.create(
        event_type=event_type,
        component="COMP-03",
        message=f"event {index}",
        data={"index": index},
        timestamp=BASE + timedelta(seconds=index),
        identifier=f"evt-{event_type}-{index}",
    )


def test_event_round_trip_and_validation() -> None:
    event = Event.create(
        event_type="agent.decision",
        component="COMP-01",
        message="decision made",
        data={"decision": "remediate"},
        timestamp=BASE,
        identifier="evt-1",
    )
    document = event.to_dict()
    assert document["timestamp"].endswith("Z")
    assert Event.from_mapping(document) == event
    with pytest.raises(ValueError):
        Event.from_mapping({**document, "level": "LOUD"})
    with pytest.raises(ValueError):
        Event.create(event_type="", component="COMP-01", message="x")
    with pytest.raises(ValueError):
        Event.create(
            event_type="tool.started", component="COMP-03", message="x", level="NOPE"
        )


def test_journal_appends_reads_and_skips_partial_lines(tmp_path) -> None:
    journal = EventJournal(tmp_path / "events.jsonl")
    publisher = JournalEventPublisher(journal)
    first = publisher.publish(
        "tool.started", component="COMP-03", message="one", data={"tool": "x"}
    )
    second = publisher.publish(
        "tool.completed", component="COMP-03", message="two", data={"tool": "x"}
    )
    assert first is not None and second is not None

    # A partially written trailing line must not surface as an event.
    with journal.path.open("ab") as handle:
        handle.write(b'{"id":"partial","timestamp":"2026-01-01T12:00:00Z"')

    page = journal.read_from(0)
    assert [event.id for event in page.events] == [first.id, second.id]
    assert page.offset == len(
        journal.path.read_text(encoding="utf-8").splitlines(keepends=True)[0]
    ) + len(journal.path.read_text(encoding="utf-8").splitlines(keepends=True)[1])

    # Reading from the returned offset continues after the complete lines.
    tail = journal.read_from(page.offset)
    assert tail.events == []

    # Once the partial write is completed, it is parsed on the next poll.
    with journal.path.open("ab") as handle:
        handle.write(b',"type":"tool.completed","component":"COMP-03",')
        handle.write(b'"level":"INFO","message":"three","data":{"tool":"x"}}\n')
    completed = journal.read_from(page.offset)
    assert len(completed.events) == 1
    assert completed.events[0].id == "partial"


def test_journal_rotation_bounds_files(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    journal = EventJournal(path, max_bytes=1024, max_files=2)
    publisher = JournalEventPublisher(journal)
    for index in range(60):
        publisher.publish(
            "system.heartbeat",
            component="COMP-01",
            message="x" * 80,
            data={"index": index},
        )
    assert path.exists()
    assert (tmp_path / "events.jsonl.1").exists()
    assert not (tmp_path / "events.jsonl.2").exists()
    assert journal.size() <= 1024


def test_follower_delivers_incrementally_and_deduplicates(tmp_path) -> None:
    journal = EventJournal(tmp_path / "events.jsonl")
    publisher = JournalEventPublisher(journal)
    seen: list[Event] = []
    follower = JournalFollower(journal, seen.append, batch_size=10)

    first = publisher.publish("tool.started", component="COMP-03", message="one")
    assert follower.poll() == 1
    assert [event.id for event in seen] == [first.id]
    assert follower.poll() == 0

    second = publisher.publish("tool.completed", component="COMP-03", message="two")
    third = publisher.publish("tool.failed", component="COMP-03", message="three")
    assert follower.poll() == 2
    assert [event.id for event in seen] == [first.id, second.id, third.id]

    # Bounded dedupe still prevents re-delivery after a reset to the start.
    follower.set_offset(0)
    assert follower.poll() == 0
    assert len(seen) == 3


def test_follower_replays_after_rotation(tmp_path) -> None:
    journal = EventJournal(tmp_path / "events.jsonl", max_bytes=1024, max_files=2)
    publisher = JournalEventPublisher(journal)
    seen: list[Event] = []
    follower = JournalFollower(journal, seen.append, batch_size=500)
    for index in range(12):
        publisher.publish(
            "system.heartbeat",
            component="COMP-01",
            message="x" * 90,
            data={"index": index},
        )
    follower.poll()
    before_rotation = len(seen)

    # Push past the rotation boundary.
    for index in range(12, 30):
        publisher.publish(
            "system.heartbeat",
            component="COMP-01",
            message="x" * 90,
            data={"index": index},
        )
    follower.poll()
    assert len(seen) > before_rotation
    # The follower must not crash or silently stop after a rotated journal.
    follower.poll()


def test_journal_limit_and_tail(tmp_path) -> None:
    journal = EventJournal(tmp_path / "events.jsonl")
    publisher = JournalEventPublisher(journal)
    for index in range(5):
        publisher.publish("tool.started", component="COMP-03", message=str(index))
    page = journal.read_from(0, limit=2)
    assert len(page.events) == 2
    assert len(journal.tail(limit=3)) == 3
    assert journal.read_all()[0].message == "0"


def test_null_publisher_returns_none() -> None:
    assert (
        NullEventPublisher().publish("tool.started", component="COMP-03", message="x")
        is None
    )


def test_bus_fans_out_and_replays() -> None:
    async def scenario() -> None:
        bus = EventBus(replay_limit=10)
        first = bus.subscribe()
        event = make_event(1)
        bus.publish(event)
        received = await asyncio.wait_for(first.get(), timeout=1)
        assert received.id == event.id

        second = bus.subscribe(replay=True)
        replay = await asyncio.wait_for(second.get(), timeout=1)
        assert replay.id == event.id
        assert bus.subscriber_count == 2
        first.close()
        second.close()
        assert bus.subscriber_count == 0
        assert bus.replay(limit=1)[0].id == event.id

    asyncio.run(scenario())


def test_bus_drops_oldest_when_subscriber_queue_is_full() -> None:
    async def scenario() -> None:
        bus = EventBus(replay_limit=10)
        subscription = bus.subscribe(max_queue=1)
        bus.publish(make_event(1))
        bus.publish(make_event(2))
        await asyncio.sleep(0)
        received = await asyncio.wait_for(subscription.get(), timeout=1)
        assert received.data["index"] == 2
        subscription.close()

    asyncio.run(scenario())


def test_journal_rejects_invalid_configuration(tmp_path) -> None:
    with pytest.raises(ValueError):
        EventJournal(tmp_path / "events.jsonl", max_bytes=10)
    with pytest.raises(ValueError):
        EventJournal(tmp_path / "events.jsonl", max_files=0)


def test_journal_corrupt_lines_are_skipped(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    journal = EventJournal(path)
    event = make_event(1)
    path.write_text(
        json.dumps(event.to_dict()) + "\nnot-json\n", encoding="utf-8"
    )
    page = journal.read_from(0)
    assert [item.id for item in page.events] == [event.id]
