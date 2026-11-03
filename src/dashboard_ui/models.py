"""Dashboard state records and public serialisation (COMP-13)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime


def parse_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


TILE_STATES = {"unknown", "healthy", "active", "pending", "error"}


@dataclass
class ServiceRecord:
    name: str
    color: str
    first_seen: datetime
    last_event_at: datetime
    query_count: int = 0
    denied_count: int = 0
    _activity: deque[datetime] = field(default_factory=deque)
    _buckets: dict[int, int] = field(default_factory=dict)

    def record_query(self, when: datetime, *, denied: bool = False) -> None:
        if denied:
            self.denied_count += 1
        else:
            self.query_count += 1
            self._activity.append(when)
            bucket = int(when.timestamp()) // 5
            self._buckets[bucket] = self._buckets.get(bucket, 0) + 1
            self._prune(when)

    def record_event(self, when: datetime) -> None:
        if when > self.last_event_at:
            self.last_event_at = when

    def _prune(self, now: datetime) -> None:
        cutoff = now.timestamp() - 600
        while self._activity and self._activity[0].timestamp() < cutoff:
            self._activity.popleft()
        oldest_bucket = int(now.timestamp()) // 5 - 120
        for bucket in [key for key in self._buckets if key < oldest_bucket]:
            del self._buckets[bucket]

    def request_rate(self, now: datetime) -> float:
        self._prune(now)
        cutoff = now.timestamp() - 60
        count = sum(1 for value in self._activity if value.timestamp() >= cutoff)
        return round(count / 60.0, 3)

    def sparkline(self, now: datetime, *, buckets: int = 12) -> list[int]:
        self._prune(now)
        current = int(now.timestamp()) // 5
        return [
            self._buckets.get(current - (buckets - 1 - index), 0)
            for index in range(buckets)
        ]

    def to_public(self, now: datetime) -> dict[str, object]:
        return {
            "name": self.name,
            "color": self.color,
            "first_seen": iso(self.first_seen),
            "last_event_at": iso(self.last_event_at),
            "query_count": self.query_count,
            "denied_count": self.denied_count,
            "request_rate": self.request_rate(now),
            "sparkline": self.sparkline(now),
        }


@dataclass
class SessionRecord:
    session_id: str
    user: str
    ip: str
    connected_at: datetime
    last_seen: datetime
    services: dict[str, str] = field(default_factory=dict)
    adapter: str | None = None
    closed_at: datetime | None = None
    new_until: datetime | None = None
    last_query: dict[str, datetime] = field(default_factory=dict)

    @property
    def open(self) -> bool:
        return self.closed_at is None

    def visible(self, now: datetime, grace_seconds: int) -> bool:
        if self.open:
            return True
        assert self.closed_at is not None
        return (now - self.closed_at).total_seconds() <= grace_seconds

    def to_public(self, now: datetime) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "user": self.user,
            "ip": self.ip,
            "connected_since": iso(self.connected_at),
            "last_seen": iso(self.last_seen),
            "service_count": len(self.services),
            "services": dict(sorted(self.services.items())),
            "status": "open" if self.open else "closing",
            "closed_at": iso(self.closed_at),
            "new": bool(self.new_until and now < self.new_until),
            "adapter": self.adapter,
        }


@dataclass
class QueryRecord:
    query_id: str
    session_id: str
    service: str
    started_at: datetime
    granted: bool


@dataclass
class TileState:
    component: str
    name: str
    status: str = "unknown"
    connected: bool = False
    message: str = "awaiting first event"
    last_event_at: datetime | None = None
    last_event_type: str | None = None
    active_since: datetime | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in TILE_STATES:
            raise ValueError(f"unknown tile status: {self.status}")

    def to_public(self) -> dict[str, object]:
        return {
            "component": self.component,
            "name": self.name,
            "status": self.status,
            "connected": self.connected,
            "message": self.message,
            "last_event_at": iso(self.last_event_at),
            "last_event_type": self.last_event_type,
            "active_since": iso(self.active_since),
            "error": self.error,
        }


@dataclass
class TraceEntry:
    timestamp: str
    kind: str
    message: str
    component: str
    data: dict[str, object] = field(default_factory=dict)

    def to_public(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "kind": self.kind,
            "message": self.message,
            "component": self.component,
            "data": self.data,
        }


class TokenBuffer:
    """Bounded Mode-B token stream sourced only from real ``agent.token`` events."""

    def __init__(self, *, max_chars: int = 20000, max_chunks: int = 500) -> None:
        self.max_chars = max_chars
        self._chunks: deque[dict[str, object]] = deque(maxlen=max_chunks)
        self._text: dict[str, str] = {}

    def add(self, *, stream_id: str, timestamp: str, token: str) -> None:
        if not token:
            return
        self._chunks.append(
            {"stream_id": stream_id, "timestamp": timestamp, "token": token}
        )
        current = self._text.get(stream_id, "") + token
        if len(current) > self.max_chars:
            current = current[-self.max_chars :]
        self._text[stream_id] = current

    def text(self, stream_id: str | None = None) -> dict[str, str]:
        if stream_id is None:
            return dict(self._text)
        value = self._text.get(stream_id)
        return {} if value is None else {stream_id: value}

    def chunks(self, *, limit: int = 100) -> list[dict[str, object]]:
        return list(self._chunks)[-limit:]
