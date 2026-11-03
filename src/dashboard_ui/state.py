"""Deterministic dashboard state reducer (COMP-13, NFR-015).

Every field in the public state is derived from a real event in the journal or
from adurable local component state (control-plane heartbeats, Prometheus
scrapes). There is no timer that invents activity: a tile only pulses because
an ``Event`` arrived, and an access-matrix cell only blinks because a real
``query.started`` event started and a matching ``query.completed``/``query.denied``
event ended it.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, cast

from common.clock import Clock, SystemClock
from dashboard_ui.config import DashboardConfig
from dashboard_ui.models import (
    QueryRecord,
    ServiceRecord,
    SessionRecord,
    TileState,
    TokenBuffer,
    TraceEntry,
    iso,
    parse_timestamp,
)
from event_bus.catalog import (
    ACTIVE_EVENTS,
    COMPLETION_EVENTS,
    COMPONENT_TILES,
    SUCCESS_EVENTS,
    TILE_IDS,
    event_tile,
    tile_for_tool,
)
from event_bus.model import Event

_TRACE_KINDS = {
    "agent.thinking": "THINKING",
    "agent.decision": "DECISION",
    "tool.started": "TOOL_CALL",
    "tool.previewed": "TOOL_CALL",
    "approval.requested": "GATE",
    "approval.granted": "GATE",
    "approval.rejected": "GATE",
    "approval.consumed": "GATE",
    "checkpoint.created": "CHECKPOINT",
    "checkpoint.restored": "CHECKPOINT",
    "tool.completed": "RESULT",
    "tool.failed": "RESULT",
    "tool.blocked": "RESULT",
    "agent.result": "RESULT",
    "incident.opened": "INCIDENT",
    "incident.remediated": "INCIDENT",
    "incident.resolved": "INCIDENT",
    "incident.escalated": "INCIDENT",
    "deployment.phase": "DEPLOY",
    "deployment.completed": "DEPLOY",
    "deployment.failed": "DEPLOY",
    "deployment.rolled_back": "DEPLOY",
    "provision.planned": "PROVISION",
    "provision.applied": "PROVISION",
    "provision.failed": "PROVISION",
    "report.generated": "REPORT",
}

_TRACE_FIELDS = (
    "tool",
    "request_id",
    "checkpoint_id",
    "service",
    "target",
    "phase",
    "status",
    "outcome",
    "incident_id",
    "attempt_count",
    "resolved",
)

_FAULT_STATUSES = {"failed", "rolled_back", "blocked"}


def _text(data: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _grants(value: object) -> dict[str, str]:
    if isinstance(value, Mapping):
        return {
            str(name): str(access)
            for name, access in value.items()
            if isinstance(name, str) and name
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return {
            str(name): "read"
            for name in value
            if isinstance(name, str) and name
        }
    return {}


class DashboardState:
    def __init__(
        self,
        config: DashboardConfig,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._clock = clock or SystemClock()
        self._lock = threading.RLock()
        self._started_at = self._clock.now()
        self._tiles: dict[str, TileState] = {
            component: TileState(component, name)
            for component, name in COMPONENT_TILES
        }
        self._sessions: dict[str, SessionRecord] = {}
        self._services: dict[str, ServiceRecord] = {}
        self._queries: dict[str, QueryRecord] = {}
        self._incidents: dict[str, dict[str, Any]] = {}
        self._approvals: dict[str, dict[str, Any]] = {}
        self._last_deployment: dict[str, Any] | None = None
        self._last_report: dict[str, Any] | None = None
        self._control_started_at: datetime | None = None
        self._control_last_heartbeat: datetime | None = None
        self._control_uptime_seconds: float | None = None
        self._control_running = False
        self._control_stopped_at: datetime | None = None
        self._adapters: dict[str, datetime] = {}
        self._latest_utilization: dict[str, dict[str, Any]] = {}
        self._recent: deque[dict[str, Any]] = deque(maxlen=config.recent_events)
        self._trace: deque[TraceEntry] = deque(maxlen=config.trace_limit)
        self._tokens = TokenBuffer(max_chars=config.token_buffer_chars)
        self._event_count = 0
        self._last_event_at: datetime | None = None
        self._stale_queries = 0
        self._seen: deque[str] = deque(maxlen=20000)
        self._seen_set: set[str] = set()

    # ------------------------------------------------------------------
    # Reducer
    # ------------------------------------------------------------------
    def apply(self, event: Event) -> bool:
        """Apply one real event. Returns False when it was already seen."""
        with self._lock:
            if event.id in self._seen_set:
                return False
            self._remember(event.id)
            now = parse_timestamp(event.timestamp)
            self._event_count += 1
            if self._last_event_at is None or now > self._last_event_at:
                self._last_event_at = now
            self._recent.append(event.to_dict())
            self._prune(now)
            self._apply_tiles(event, now)
            self._apply_control(event, now)
            self._apply_sessions(event, now)
            self._apply_incidents(event, now)
            self._apply_approvals(event, now)
            self._apply_deployments(event, now)
            self._apply_metrics(event, now)
            self._apply_report(event, now)
            self._apply_trace(event)
            if event.type == "agent.token":
                token = _text(event.data, "token", "text", "delta")
                if token:
                    stream_id = _text(event.data, "stream_id", "request_id") or "agent"
                    self._tokens.add(
                        stream_id=stream_id,
                        timestamp=event.timestamp,
                        token=token,
                    )
            return True

    def _remember(self, event_id: str) -> None:
        if len(self._seen) == self._seen.maxlen:
            oldest = self._seen.popleft()
            self._seen_set.discard(oldest)
        self._seen.append(event_id)
        self._seen_set.add(event_id)

    def _apply_tiles(self, event: Event, now: datetime) -> None:
        targets: list[str] = []
        primary = event_tile(event.type, event.component, event.data)
        if primary:
            targets.append(primary)
        if event.type.startswith("tool."):
            mapped = tile_for_tool(str(event.data.get("tool", "")))
            if mapped and mapped not in targets:
                targets.append(mapped)
        if event.component in TILE_IDS and event.component not in targets:
            # An event may explicitly name a tile (e.g. deployment phase).
            targets.append(event.component)
        for component in targets:
            tile = self._tiles.get(component)
            if tile is None:
                continue
            tile.connected = True
            tile.last_event_at = now
            tile.last_event_type = event.type
            tile.message = event.message[:200]
            self._update_tile_status(tile, event, now)

    def _update_tile_status(self, tile: TileState, event: Event, now: datetime) -> None:
        status = event.data.get("status")
        if event.type == "approval.requested":
            tile.status = "pending"
            tile.active_since = now
            tile.error = None
            return
        if event.type in {"approval.granted", "approval.rejected", "approval.consumed"}:
            tile.status = "healthy"
            tile.active_since = None
            tile.error = event.message if event.type == "approval.rejected" else None
            return
        if tile.component == "COMP-07":
            if event.type in {"incident.opened", "incident.escalated", "alert.firing"}:
                tile.status = "error"
                tile.error = event.message
                tile.active_since = now
                return
            if event.type == "incident.resolved":
                tile.status = "healthy"
                tile.error = None
                tile.active_since = None
                return
            if event.type == "incident.remediated":
                tile.status = "active" if not event.data.get("resolved") else "healthy"
                return
            if event.type == "health.poll":
                if event.data.get("triggered"):
                    tile.status = "error"
                    tile.error = event.message
                return
        if event.type in {
            "deployment.failed",
            "deployment.rolled_back",
            "provision.failed",
            "proxy.failed",
            "tool.failed",
            "tool.rollback_failed",
            "metrics.scrape_failed",
            "checkpoint.restore_failed",
        }:
            tile.status = "error"
            tile.error = event.message
            tile.active_since = None
            return
        if event.type in ACTIVE_EVENTS:
            tile.status = "active"
            tile.active_since = now
            tile.error = None
            return
        if event.type in COMPLETION_EVENTS or event.type in SUCCESS_EVENTS:
            tile.status = "healthy"
            tile.active_since = None
            tile.error = None
            return
        if event.level in {"ERROR", "CRITICAL"} and tile.component not in {"COMP-12"}:
            tile.status = "error"
            tile.error = event.message
            return
        if tile.status == "unknown":
            tile.status = "healthy"
        if status == "failed":
            tile.status = "error"
            tile.error = event.message

    def _apply_control(self, event: Event, now: datetime) -> None:
        if event.type == "control.started":
            self._control_started_at = now
            self._control_running = True
            self._control_stopped_at = None
        elif event.type == "control.stopped":
            self._control_running = False
            self._control_stopped_at = now
        elif event.type == "system.heartbeat":
            self._control_last_heartbeat = now
            self._control_running = True
            self._control_stopped_at = None
            uptime = event.data.get("uptime_seconds")
            if isinstance(uptime, (int, float)):
                self._control_uptime_seconds = float(uptime)

    def _apply_sessions(self, event: Event, now: datetime) -> None:
        data = event.data
        if event.type == "session.opened":
            session_id = _text(data, "session_id", "id")
            if not session_id:
                return
            self._touch_adapter(data, now)
            user = _text(data, "user", "username", "account") or "unknown"
            ip = _text(data, "ip", "client_ip", "remote_ip") or ""
            services = _grants(data.get("services", data.get("grants")))
            session = self._sessions.get(session_id)
            if session is None:
                session = SessionRecord(
                    session_id=session_id,
                    user=user,
                    ip=ip,
                    connected_at=now,
                    last_seen=now,
                    services=services,
                    adapter=_text(data, "adapter"),
                    new_until=now + timedelta(seconds=10),
                )
                self._sessions[session_id] = session
            else:
                session.user = user
                session.ip = ip
                session.last_seen = now
                session.closed_at = None
                session.services.update(services)
                session.adapter = _text(data, "adapter") or session.adapter
                session.new_until = now + timedelta(seconds=10)
            for service in services:
                self._ensure_service(service, now).record_event(now)
            return
        if event.type == "session.heartbeat":
            session_id = _text(data, "session_id", "id")
            if not session_id:
                return
            self._touch_adapter(data, now)
            session = self._sessions.get(session_id)
            if session is not None and session.open:
                session.last_seen = now
            return
        if event.type == "session.closed":
            session_id = _text(data, "session_id", "id")
            if not session_id:
                return
            self._touch_adapter(data, now)
            session = self._sessions.get(session_id)
            if session is not None and session.open:
                session.closed_at = now
                session.last_seen = now
            return
        if event.type == "query.started":
            self._apply_query_start(data, now)
            return
        if event.type in {"query.completed", "query.denied"}:
            self._apply_query_end(data, now, denied=event.type == "query.denied")

    def _apply_query_start(self, data: Mapping[str, Any], now: datetime) -> None:
        self._touch_adapter(data, now)
        query_id = _text(data, "query_id", "id")
        session_id = _text(data, "session_id")
        service = _text(data, "service", "database", "target")
        if not query_id or not session_id or not service:
            return
        session = self._sessions.get(session_id)
        if session is None:
            # A query without an observed session cannot be attributed to a
            # user honestly; the adapter contract requires session.opened first.
            return
        self._ensure_service(service, now).record_event(now)
        granted = service in session.services
        self._queries[query_id] = QueryRecord(
            query_id=query_id,
            session_id=session_id,
            service=service,
            started_at=now,
            granted=granted,
        )
        session.last_query[service] = now
        if granted:
            self._services[service].record_query(now)

    def _apply_query_end(
        self, data: Mapping[str, Any], now: datetime, *, denied: bool
    ) -> None:
        self._touch_adapter(data, now)
        query_id = _text(data, "query_id", "id")
        query = self._queries.pop(query_id, None) if query_id else None
        if query is None:
            # A completion whose start predates the dashboard's window still
            # proves real activity; count it for the service rate but do not
            # invent a blink that never had a start event.
            session_id = _text(data, "session_id")
            service = _text(data, "service", "database", "target")
            session = self._sessions.get(session_id) if session_id else None
            if session is not None and service:
                record = self._ensure_service(service, now)
                record.record_event(now)
                session.last_query[service] = now
                if service in session.services and not denied:
                    record.record_query(now)
                if denied:
                    record.record_query(now, denied=True)
            return
        service_record = self._services.get(query.service)
        if service_record is not None:
            service_record.record_event(now)
            if denied:
                service_record.record_query(now, denied=True)
        session = self._sessions.get(query.session_id)
        if session is not None:
            session.last_query[query.service] = now

    def _apply_incidents(self, event: Event, now: datetime) -> None:
        if event.type not in {
            "incident.opened",
            "incident.remediated",
            "incident.resolved",
            "incident.escalated",
        }:
            return
        key = _text(event.data, "incident_id") or event.id
        if event.type == "incident.opened":
            self._incidents[key] = {
                "incident_id": key,
                "service": _text(event.data, "service"),
                "app": _text(event.data, "app"),
                "summary": event.message,
                "status": "open",
                "opened_at": event.timestamp,
            }
            return
        if event.type == "incident.resolved":
            self._incidents.pop(key, None)
            return
        current = self._incidents.get(key)
        if current is not None:
            current["status"] = (
                "escalated" if event.type == "incident.escalated" else "remediated"
            )
            current["last_event_at"] = event.timestamp
        elif event.type == "incident.escalated":
            self._incidents[key] = {
                "incident_id": key,
                "service": _text(event.data, "service"),
                "app": _text(event.data, "app"),
                "summary": event.message,
                "status": "escalated",
                "opened_at": event.timestamp,
            }

    def _apply_approvals(self, event: Event, now: datetime) -> None:
        if event.type == "approval.requested":
            request_id = _text(event.data, "request_id")
            if not request_id:
                return
            self._approvals[request_id] = {
                "request_id": request_id,
                "tool_name": _text(event.data, "tool_name"),
                "risk_categories": list(event.data.get("risk_categories", [])),
                "requested_at": event.timestamp,
                "rollback_plan": _text(event.data, "rollback_plan"),
            }
            return
        if event.type in {"approval.granted", "approval.rejected", "approval.consumed"}:
            request_id = _text(event.data, "request_id")
            if request_id:
                self._approvals.pop(request_id, None)

    def _apply_deployments(self, event: Event, now: datetime) -> None:
        if event.type not in {
            "deployment.started",
            "deployment.phase",
            "deployment.completed",
            "deployment.failed",
            "deployment.rolled_back",
        }:
            return
        status = "in_progress"
        if event.type == "deployment.completed":
            status = "success"
        elif event.type == "deployment.failed":
            status = "failed"
        elif event.type == "deployment.rolled_back":
            status = "rolled_back"
        elif event.type == "deployment.phase":
            status = str(event.data.get("status", "in_progress"))
        self._last_deployment = {
            "app": _text(event.data, "app"),
            "version": _text(event.data, "version"),
            "slot": _text(event.data, "slot"),
            "phase": _text(event.data, "phase"),
            "status": status,
            "message": event.message,
            "at": event.timestamp,
        }

    def _apply_metrics(self, event: Event, now: datetime) -> None:
        if event.type != "metrics.sample":
            return
        app = _text(event.data, "app") or "host"
        self._latest_utilization[app] = {
            "app": app,
            "host": _text(event.data, "host"),
            "cpu_percent": event.data.get("cpu_percent"),
            "memory_mb": event.data.get("memory_mb"),
            "disk_percent": event.data.get("disk_percent"),
            "at": event.timestamp,
        }

    def _apply_report(self, event: Event, now: datetime) -> None:
        if event.type != "report.generated":
            return
        self._last_report = {
            "path": _text(event.data, "path"),
            "period_start": _text(event.data, "period_start"),
            "period_end": _text(event.data, "period_end"),
            "incidents": event.data.get("incidents"),
            "remediations": event.data.get("remediations"),
            "at": event.timestamp,
        }

    def _apply_trace(self, event: Event) -> None:
        kind = _TRACE_KINDS.get(event.type)
        if kind is None:
            return
        data = {
            key: event.data[key]
            for key in _TRACE_FIELDS
            if key in event.data and event.data[key] is not None
        }
        self._trace.append(
            TraceEntry(
                timestamp=event.timestamp,
                kind=kind,
                message=event.message,
                component=event.component,
                data=data,
            )
        )

    def _touch_adapter(self, data: Mapping[str, Any], now: datetime) -> None:
        adapter = _text(data, "adapter")
        if adapter:
            self._adapters[adapter] = now

    def _ensure_service(self, name: str, now: datetime) -> ServiceRecord:
        record = self._services.get(name)
        if record is None:
            index = len(self._services) % len(self._config.palette)
            record = ServiceRecord(
                name=name,
                color=self._config.palette[index],
                first_seen=now,
                last_event_at=now,
            )
            self._services[name] = record
        return record

    def _prune(self, now: datetime) -> None:
        closed = [
            session_id
            for session_id, session in self._sessions.items()
            if not session.visible(now, self._config.session_grace_seconds)
            or (
                session.open
                and (now - session.last_seen).total_seconds()
                > self._config.session_idle_seconds
            )
        ]
        for session_id in closed:
            del self._sessions[session_id]
        stale_queries = [
            query_id
            for query_id, query in self._queries.items()
            if (now - query.started_at).total_seconds() > self._config.query_stale_seconds
        ]
        for query_id in stale_queries:
            del self._queries[query_id]
        self._stale_queries += len(stale_queries)

    # ------------------------------------------------------------------
    # Public state
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock.now()
            self._prune(now)
            sessions = sorted(
                (
                    session
                    for session in self._sessions.values()
                    if session.visible(now, self._config.session_grace_seconds)
                ),
                key=lambda session: session.connected_at,
            )
            services = sorted(self._services.values(), key=lambda item: item.first_seen)
            cells = []
            for session in sessions:
                for service in services:
                    active = any(
                        query.session_id == session.session_id
                        and query.service == service.name
                        for query in self._queries.values()
                    )
                    if active:
                        cell_state = "active"
                    elif service.name in session.services:
                        cell_state = "idle"
                    else:
                        cell_state = "no_access"
                    cells.append(
                        {
                            "session_id": session.session_id,
                            "service": service.name,
                            "state": cell_state,
                            "access": session.services.get(service.name),
                            "last_query_at": iso(session.last_query.get(service.name)),
                        }
                    )
            active_queries = [
                {
                    "query_id": query.query_id,
                    "session_id": query.session_id,
                    "service": query.service,
                    "granted": query.granted,
                    "started_at": iso(query.started_at),
                }
                for query in self._queries.values()
            ]
            control_recent = self._control_last_heartbeat is not None and (
                now - self._control_last_heartbeat
            ).total_seconds() <= 30
            uptime = self._control_uptime_seconds
            if uptime is None:
                if self._control_started_at is not None:
                    uptime = (now - self._control_started_at).total_seconds()
                else:
                    uptime = (now - self._started_at).total_seconds()
            return {
                "server_time": iso(now),
                "dashboard_started_at": iso(self._started_at),
                "event_count": self._event_count,
                "last_event_at": iso(self._last_event_at),
                "tiles": [self._tiles[component].to_public() for component, _ in COMPONENT_TILES],
                "services": [service.to_public(now) for service in services],
                "users": [session.to_public(now) for session in sessions],
                "cells": cells,
                "active_queries": active_queries,
                "query_adapter": self._adapter_public(now),
                "summary": {
                    "control": {
                        "running": self._control_running and (control_recent or self._control_last_heartbeat is None),
                        "started_at": iso(self._control_started_at),
                        "stopped_at": iso(self._control_stopped_at),
                        "last_heartbeat": iso(self._control_last_heartbeat),
                        "heartbeat_fresh": control_recent,
                        "uptime_seconds": round(max(0.0, uptime), 1),
                    },
                    "active_incidents": len(self._incidents),
                    "incidents": sorted(
                        self._incidents.values(), key=lambda item: str(item.get("opened_at"))
                    ),
                    "connected_users": sum(1 for session in sessions if session.open),
                    "total_users": len(sessions),
                    "total_services": len(services),
                    "pending_approvals": len(self._approvals),
                    "approvals": sorted(
                        self._approvals.values(),
                        key=lambda item: str(item.get("requested_at")),
                    ),
                    "last_deployment": self._last_deployment,
                    "weekly_report": self._last_report,
                    "latest_utilization": dict(sorted(self._latest_utilization.items())),
                    "active_queries": len(self._queries),
                    "stale_queries": self._stale_queries,
                    "events_seen": self._event_count,
                },
                "recent_events": list(self._recent)[-100:],
            }

    def _adapter_public(self, now: datetime) -> dict[str, Any]:
        if not self._adapters:
            reason = (
                "ingest enabled; awaiting a named per-database adapter event"
                if self._config.ingest_enabled
                else "ingest disabled; no per-database adapter configured (ADR-008)"
            )
            return {
                "connected": False,
                "adapter": None,
                "last_event_at": None,
                "reason": reason,
            }
        adapter, last_seen = max(self._adapters.items(), key=lambda item: item[1])
        connected = (now - last_seen).total_seconds() <= max(
            120, self._config.session_grace_seconds * 2
        )
        return {
            "connected": connected,
            "adapter": adapter,
            "last_event_at": iso(last_seen),
            "reason": None
            if connected
            else "last adapter event is stale; query blink is not connected",
        }

    def events(self, *, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._recent)[-limit:]

    def trace(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            return [entry.to_public() for entry in list(self._trace)[-limit:]]

    def tokens(self, *, stream_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            return {
                "text": self._tokens.text(stream_id),
                "chunks": self._tokens.chunks(limit=100),
            }

    def summary(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.to_dict()["summary"])

    # ------------------------------------------------------------------
    # Snapshot persistence
    # ------------------------------------------------------------------
    def to_store(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock.now()
            self._prune(now)
            return {
                "version": 1,
                "saved_at": iso(now),
                "event_count": self._event_count,
                "last_event_at": iso(self._last_event_at),
                "sessions": [
                    {
                        "session_id": session.session_id,
                        "user": session.user,
                        "ip": session.ip,
                        "connected_at": iso(session.connected_at),
                        "last_seen": iso(session.last_seen),
                        "services": dict(session.services),
                        "adapter": session.adapter,
                        "closed_at": iso(session.closed_at),
                        "new_until": iso(session.new_until),
                        "last_query": {
                            name: iso(value) for name, value in session.last_query.items()
                        },
                    }
                    for session in self._sessions.values()
                ],
                "services": [
                    {
                        "name": record.name,
                        "color": record.color,
                        "first_seen": iso(record.first_seen),
                        "last_event_at": iso(record.last_event_at),
                        "query_count": record.query_count,
                        "denied_count": record.denied_count,
                    }
                    for record in self._services.values()
                ],
                "queries": [
                    {
                        "query_id": query.query_id,
                        "session_id": query.session_id,
                        "service": query.service,
                        "started_at": iso(query.started_at),
                        "granted": query.granted,
                    }
                    for query in self._queries.values()
                ],
                "incidents": list(self._incidents.values()),
                "approvals": list(self._approvals.values()),
                "last_deployment": self._last_deployment,
                "last_report": self._last_report,
                "control": {
                    "started_at": iso(self._control_started_at),
                    "stopped_at": iso(self._control_stopped_at),
                    "last_heartbeat": iso(self._control_last_heartbeat),
                    "running": self._control_running,
                    "uptime_seconds": self._control_uptime_seconds,
                },
                "adapters": {
                    name: iso(value) for name, value in self._adapters.items()
                },
                "tiles": {
                    component: {
                        "status": tile.status,
                        "connected": tile.connected,
                        "message": tile.message,
                        "last_event_at": iso(tile.last_event_at),
                        "last_event_type": tile.last_event_type,
                        "active_since": iso(tile.active_since),
                        "error": tile.error,
                    }
                    for component, tile in self._tiles.items()
                },
                "stale_queries": self._stale_queries,
            }

    def restore(self, payload: Mapping[str, Any]) -> bool:
        if payload.get("version") != 1:
            return False
        try:
            with self._lock:
                self._event_count = int(payload.get("event_count", 0))
                saved_at = payload.get("last_event_at")
                self._last_event_at = parse_timestamp(saved_at) if saved_at else None
                self._sessions = {}
                for document in payload.get("sessions", []):
                    session = SessionRecord(
                        session_id=str(document["session_id"]),
                        user=str(document["user"]),
                        ip=str(document["ip"]),
                        connected_at=parse_timestamp(document["connected_at"]),
                        last_seen=parse_timestamp(document["last_seen"]),
                        services={
                            str(name): str(access)
                            for name, access in document.get("services", {}).items()
                        },
                        adapter=document.get("adapter"),
                        closed_at=parse_timestamp(document["closed_at"])
                        if document.get("closed_at")
                        else None,
                        new_until=parse_timestamp(document["new_until"])
                        if document.get("new_until")
                        else None,
                        last_query={
                            str(name): parse_timestamp(value)
                            for name, value in document.get("last_query", {}).items()
                        },
                    )
                    self._sessions[session.session_id] = session
                self._services = {}
                for document in payload.get("services", []):
                    record = ServiceRecord(
                        name=str(document["name"]),
                        color=str(document["color"]),
                        first_seen=parse_timestamp(document["first_seen"]),
                        last_event_at=parse_timestamp(document["last_event_at"]),
                        query_count=int(document.get("query_count", 0)),
                        denied_count=int(document.get("denied_count", 0)),
                    )
                    self._services[record.name] = record
                self._queries = {}
                for document in payload.get("queries", []):
                    query = QueryRecord(
                        query_id=str(document["query_id"]),
                        session_id=str(document["session_id"]),
                        service=str(document["service"]),
                        started_at=parse_timestamp(document["started_at"]),
                        granted=bool(document.get("granted", False)),
                    )
                    self._queries[query.query_id] = query
                self._incidents = {
                    str(document.get("incident_id")): dict(document)
                    for document in payload.get("incidents", [])
                    if document.get("incident_id")
                }
                self._approvals = {
                    str(document.get("request_id")): dict(document)
                    for document in payload.get("approvals", [])
                    if document.get("request_id")
                }
                self._last_deployment = payload.get("last_deployment")
                self._last_report = payload.get("last_report")
                control = payload.get("control", {})
                self._control_started_at = (
                    parse_timestamp(control["started_at"])
                    if control.get("started_at")
                    else None
                )
                self._control_stopped_at = (
                    parse_timestamp(control["stopped_at"])
                    if control.get("stopped_at")
                    else None
                )
                self._control_last_heartbeat = (
                    parse_timestamp(control["last_heartbeat"])
                    if control.get("last_heartbeat")
                    else None
                )
                self._control_running = bool(control.get("running", False))
                uptime = control.get("uptime_seconds")
                self._control_uptime_seconds = (
                    float(uptime) if isinstance(uptime, (int, float)) else None
                )
                self._adapters = {
                    str(name): parse_timestamp(value)
                    for name, value in payload.get("adapters", {}).items()
                }
                self._stale_queries = int(payload.get("stale_queries", 0))
                for component, document in payload.get("tiles", {}).items():
                    tile = self._tiles.get(component)
                    if tile is None:
                        continue
                    tile.status = (
                        str(document.get("status", "unknown"))
                        if document.get("status") in {"unknown", "healthy", "active", "pending", "error"}
                        else "unknown"
                    )
                    tile.connected = bool(document.get("connected", False))
                    tile.message = str(document.get("message", tile.message))
                    tile.last_event_type = document.get("last_event_type")
                    tile.last_event_at = (
                        parse_timestamp(document["last_event_at"])
                        if document.get("last_event_at")
                        else None
                    )
                    tile.active_since = (
                        parse_timestamp(document["active_since"])
                        if document.get("active_since")
                        else None
                    )
                    tile.error = document.get("error")
                # Restored sessions are not "new" on a fresh page load.
                for session in self._sessions.values():
                    session.new_until = None
                return True
        except (KeyError, TypeError, ValueError):
            return False
