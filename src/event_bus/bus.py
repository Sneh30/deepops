"""In-process asyncio pub/sub used by COMP-13 (IMPL-701).

The bus is process-local by design (SAS.md §5.1). The durable journal is the
cross-process transport; this bus only fans events out to connected SSE
clients inside the dashboard process.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import Iterable

from event_bus.model import Event


class Subscription:
    def __init__(
        self,
        bus: EventBus,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[Event],
    ) -> None:
        self._bus = bus
        self._loop = loop
        self._queue = queue
        self._closed = False

    @property
    def queue(self) -> asyncio.Queue[Event]:
        return self._queue

    async def get(self) -> Event:
        return await self._queue.get()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._bus.unsubscribe(self._loop, self._queue)

    def __enter__(self) -> Subscription:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class EventBus:
    def __init__(self, *, replay_limit: int = 500) -> None:
        if replay_limit < 1:
            raise ValueError("replay_limit must be positive")
        self._replay: deque[Event] = deque(maxlen=replay_limit)
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[Event]]] = []

    def subscribe(
        self, *, replay: bool = False, max_queue: int = 1000
    ) -> Subscription:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=max_queue)
        self._subscribers.append((loop, queue))
        if replay:
            for event in self._replay:
                self._offer(queue, event)
        return Subscription(self, loop, queue)

    def unsubscribe(
        self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[Event]
    ) -> None:
        try:
            self._subscribers.remove((loop, queue))
        except ValueError:
            return

    def publish(self, event: Event) -> None:
        self._replay.append(event)
        for loop, queue in list(self._subscribers):
            if loop.is_closed():
                self.unsubscribe(loop, queue)
                continue
            if loop.is_running():
                loop.call_soon_threadsafe(self._offer, queue, event)
            else:  # tests may publish before the loop starts
                self._offer(queue, event)

    @staticmethod
    def _offer(queue: asyncio.Queue[Event], event: Event) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(event)
            return
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(event)

    def replay(self, *, limit: int | None = None) -> list[Event]:
        events: Iterable[Event] = self._replay
        if limit is not None:
            return list(events)[-limit:]
        return list(events)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
