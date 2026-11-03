"""Mission Control dashboard service orchestration (COMP-13, IMPL-703)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from common.clock import Clock, SystemClock
from dashboard_ui.config import DashboardConfig
from dashboard_ui.logs import LogFeed
from dashboard_ui.models import iso
from dashboard_ui.snapshot import SnapshotStore
from dashboard_ui.state import DashboardState
from event_bus.bus import EventBus
from event_bus.follower import JournalFollower
from event_bus.journal import EventJournal
from event_bus.model import Event
from event_bus.publisher import EventPublisher, JournalEventPublisher
from observability.prometheus import MetricStore, ObservabilityLoop, PrometheusClient

# These events only pulse a tile / append a trace or ticker line. Sending the
# whole structural state for each token or scrape would add cost without adding
# information, so the client applies the event directly (the state is still
# updated server-side and available through /api/state).
PULSE_ONLY_EVENTS = frozenset(
    {
        "agent.token",
        "agent.thinking",
        "agent.decision",
        "agent.result",
        "health.poll",
        "metrics.sample",
        "system.heartbeat",
        "tool.retry",
    }
)

_SESSION_EVENTS = {
    "opened": "session.opened",
    "open": "session.opened",
    "heartbeat": "session.heartbeat",
    "closed": "session.closed",
    "close": "session.closed",
}
_QUERY_EVENTS = {
    "started": "query.started",
    "start": "query.started",
    "completed": "query.completed",
    "complete": "query.completed",
    "denied": "query.denied",
}


class IngestError(ValueError):
    """A rejected adapter payload; the HTTP layer turns this into a 4xx."""

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


class DashboardService:
    def __init__(
        self,
        config: DashboardConfig,
        *,
        data_dir: str | Path | None = None,
        state: DashboardState | None = None,
        journal: EventJournal | None = None,
        publisher: EventPublisher | None = None,
        bus: EventBus | None = None,
        follower: JournalFollower | None = None,
        snapshot_store: SnapshotStore | None = None,
        metrics: MetricStore | None = None,
        observability: ObservabilityLoop | None = None,
        log_feed: LogFeed | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.config = config
        self.clock = clock or SystemClock()
        self._started_at = self.clock.now()
        self.data_dir = (
            Path(data_dir)
            if data_dir is not None
            else Path(config.journal_path).parent.parent
        )
        self.journal = journal or EventJournal(
            config.journal_path,
            max_bytes=config.journal_max_bytes,
            max_files=config.journal_max_files,
        )
        self.publisher = publisher or JournalEventPublisher(self.journal, clock=self.clock)
        self.state = state or DashboardState(config, clock=self.clock)
        self.bus = bus or EventBus(replay_limit=max(50, config.recent_events))
        self.snapshot_store = snapshot_store or SnapshotStore(config.snapshot_path)
        self.metrics = metrics or MetricStore()
        self.observability = observability or ObservabilityLoop(
            PrometheusClient(
                config.prometheus_url,
                timeout_seconds=config.prometheus_timeout_seconds,
            ),
            self.metrics,
            {query.name: query.query for query in config.metric_queries},
            publisher=self.publisher,
            interval_seconds=config.scrape_interval_seconds,
            clock=self.clock,
        )
        self.log_feed = log_feed or LogFeed(
            self.data_dir / "logs",
            glob=config.log_glob,
            audit_path=self.data_dir / "logs" / "audit.jsonl",
            limit=config.log_limit,
        )
        self.follower = follower or JournalFollower(
            self.journal,
            self._on_event,
            poll_interval_seconds=config.poll_interval_seconds,
            batch_size=max(100, config.recent_events),
        )
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._lock = threading.RLock()
        self._last_snapshot_at: datetime | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        snapshot = self.snapshot_store.load()
        restored = False
        if snapshot is not None:
            restored = self.state.restore(snapshot.state)
            if restored:
                self.follower.set_offset(snapshot.journal_offset)
        if not restored:
            # No usable snapshot: replay the current journal from the start so
            # a restarted dashboard immediately reconstructs recent sessions.
            self.follower.set_offset(0)
        self._stop = asyncio.Event()
        self._tasks = [
            asyncio.create_task(self.follower.run(self._stop), name="dashboard-journal")
        ]
        self._tasks.append(
            asyncio.create_task(self.observability.run(self._stop), name="dashboard-metrics")
        )
        self._running = True
        self._last_snapshot_at = self.clock.now()

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []
        self.save_snapshot()

    def refresh(self) -> int:
        """Synchronously apply journal events (used by tests and ingest)."""
        return self.follower.poll()

    def save_snapshot(self) -> None:
        try:
            self.snapshot_store.save(
                journal_offset=self.follower.offset,
                state=self.state.to_store(),
            )
            self._last_snapshot_at = self.clock.now()
        except OSError:
            # A snapshot failure degrades restart backfill; it must never
            # affect the live stream or the Control Plane (NFR-014).
            return

    def _maybe_snapshot(self) -> None:
        last = self._last_snapshot_at
        now = self.clock.now()
        if last is None or (now - last).total_seconds() >= self.config.snapshot_interval_seconds:
            self.save_snapshot()

    def _on_event(self, event: Event) -> None:
        self.state.apply(event)
        self.bus.publish(event)
        self._maybe_snapshot()

    # ------------------------------------------------------------------
    # Read surfaces
    # ------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "COMP-13",
            "running": self._running,
            "started_at": iso(self._started_at),
            "uptime_seconds": round((self.clock.now() - self._started_at).total_seconds(), 1),
            "journal": {
                "path": str(self.journal.path),
                "size_bytes": self.journal.size(),
                "offset": self.follower.offset,
            },
            "subscribers": self.bus.subscriber_count,
            "event_count": self.state.to_dict()["event_count"],
            "metrics_connected": self.metrics.connected,
        }

    def state_payload(self) -> dict[str, Any]:
        return self.state.to_dict()

    def summary(self) -> dict[str, Any]:
        return self.state.summary()

    def recent_events(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return self.state.events(limit=limit)

    def trace(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return self.state.trace(limit=limit)

    def tokens(self, *, stream_id: str | None = None) -> dict[str, Any]:
        return self.state.tokens(stream_id=stream_id)

    def metrics_payload(self, *, points: int = 120) -> dict[str, Any]:
        payload = self.metrics.snapshot(points=points)
        payload["utilization"] = self.state.to_dict()["summary"]["latest_utilization"]
        return payload

    def logs(
        self,
        *,
        level: str | None = None,
        component: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return self.log_feed.query(level=level, component=component, limit=limit)

    # ------------------------------------------------------------------
    # Adapter ingest (ADR-008 extension point, not a fake adapter)
    # ------------------------------------------------------------------
    def ingest_session(self, payload: Mapping[str, Any]) -> Event | None:
        action = str(payload.get("event", "")).lower()
        event_type = _SESSION_EVENTS.get(action)
        if event_type is None:
            raise IngestError("event must be one of opened|heartbeat|closed")
        session_id = _required(payload, "session_id")
        adapter = _required(payload, "adapter")
        data: dict[str, Any] = {
            "session_id": session_id,
            "adapter": adapter,
        }
        if event_type == "session.opened":
            user = _optional(payload, "user", max_length=128)
            if not user:
                raise IngestError("user is required when opening a session")
            data["user"] = user
            data["ip"] = _optional(payload, "ip", max_length=64) or ""
            services = payload.get("services", {})
            if isinstance(services, Mapping):
                data["services"] = {
                    str(name): str(access)
                    for name, access in services.items()
                    if isinstance(name, str) and name
                }
            elif isinstance(services, list):
                data["services"] = [
                    str(name) for name in services if isinstance(name, str) and name
                ]
            else:
                raise IngestError("services must be an object of name->access or a list")
        return self.publisher.publish(
            event_type,
            component="COMP-13",
            message=f"session {action}: {data.get('user', session_id)}",
            data=data,
        )

    def ingest_query(self, payload: Mapping[str, Any]) -> Event | None:
        action = str(payload.get("event", "")).lower()
        event_type = _QUERY_EVENTS.get(action)
        if event_type is None:
            raise IngestError("event must be one of started|completed|denied")
        query_id = _required(payload, "query_id")
        session_id = _required(payload, "session_id")
        service = _required(payload, "service")
        adapter = _required(payload, "adapter")
        data: dict[str, Any] = {
            "query_id": query_id,
            "session_id": session_id,
            "service": service,
            "adapter": adapter,
        }
        access = _optional(payload, "access", max_length=32)
        if access:
            data["access"] = access
        return self.publisher.publish(
            event_type,
            component="COMP-13",
            message=f"query {action}: {service}",
            data=data,
        )

    # ------------------------------------------------------------------
    # SSE
    # ------------------------------------------------------------------
    async def stream(self, request: Any) -> Any:
        subscription = self.bus.subscribe()
        initial_state = self.state_payload()

        async def generator() -> Any:
            try:
                yield "retry: 2000\n\n"
                yield _sse("state", initial_state)
                while True:
                    try:
                        event = await asyncio.wait_for(subscription.get(), timeout=15.0)
                    except TimeoutError:
                        if await request.is_disconnected():
                            return
                        yield ": keep-alive\n\n"
                        continue
                    if event.type in PULSE_ONLY_EVENTS:
                        yield _sse("pulse", {"event": event.to_dict()}, event_id=event.id)
                    else:
                        yield _sse(
                            "update",
                            {"event": event.to_dict(), "state": self.state_payload()},
                            event_id=event.id,
                        )
            finally:
                subscription.close()

        return generator()


def _sse(event: str, payload: Mapping[str, Any], *, event_id: str | None = None) -> str:
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append("data: " + json.dumps(payload, separators=(",", ":"), default=str))
    return "\n".join(lines) + "\n\n"


def _required(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise IngestError(f"{name} is required")
    if len(value) > 256:
        raise IngestError(f"{name} is too long")
    return value.strip()


def _optional(payload: Mapping[str, Any], name: str, *, max_length: int) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise IngestError(f"{name} must be a string")
    if len(value) > max_length:
        raise IngestError(f"{name} is too long")
    return value.strip()
