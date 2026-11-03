"""Mission Control dashboard tests (COMP-13, UI_SPEC.md, IMPL-701-713)."""

from __future__ import annotations

import asyncio
import itertools
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from common.config import REPO_ROOT
from common.errors import ConfigValidationError
from dashboard_ui.app import create_app
from dashboard_ui.config import DashboardConfig, MetricQuery
from dashboard_ui.service import DashboardService
from dashboard_ui.snapshot import SnapshotStore
from dashboard_ui.state import DashboardState
from event_bus.model import Event

DEFAULT_CONFIG = REPO_ROOT / "config" / "defaults" / "dashboard.yaml"
FRONTEND = REPO_ROOT / "frontend" / "dashboard"
BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_ids = itertools.count()


def make_config(tmp_path: Path, **overrides) -> DashboardConfig:
    config = DashboardConfig.load(DEFAULT_CONFIG, data_dir=tmp_path)
    # Tests never contact a real Prometheus instance.
    values = {"metric_queries": (), **overrides}
    return replace(config, **values)


def make_event(
    event_type: str,
    *,
    component: str = "COMP-13",
    data: dict | None = None,
    level: str = "INFO",
    message: str | None = None,
    timestamp: datetime = BASE_TIME,
) -> Event:
    return Event.create(
        event_type=event_type,
        component=component,
        message=message or event_type,
        data=data or {},
        level=level,
        timestamp=timestamp,
        identifier=f"evt-test-{next(_ids)}",
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_default_dashboard_config_resolves_data_dir_paths(tmp_path) -> None:
    config = DashboardConfig.load(DEFAULT_CONFIG, data_dir=tmp_path)
    assert config.host == "127.0.0.1"
    assert config.port == 8090
    assert config.path_prefix == "/mission-control"
    assert config.journal_path == tmp_path / "logs" / "events.jsonl"
    assert config.snapshot_path == tmp_path / "dashboard" / "state.json"
    assert config.ingest_enabled is False
    assert config.ingest_token_env == "OPSPILOT_DASHBOARD_INGEST_TOKEN"
    assert len(config.palette) >= 8
    assert {query.name for query in config.metric_queries} == {
        "cpu_percent",
        "memory_available_bytes",
        "disk_free_bytes",
        "http_requests_per_second",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("port", 70000),
        ("path_prefix", "mission-control"),
        ("path_prefix", "/mission control"),
        ("session_grace_seconds", 601),
        ("session_idle_seconds", 59),
        ("query_stale_seconds", 0),
        ("recent_events", 5),
        ("trace_limit", 2),
        ("token_buffer_chars", 10),
        ("snapshot_interval_seconds", 0),
        ("prometheus_url", "ftp://prometheus"),
        ("prometheus_timeout_seconds", 500),
        ("scrape_interval_seconds", 1),
        ("max_request_bytes", 10),
        ("log_limit", 0),
    ],
)
def test_dashboard_config_rejects_out_of_range_values(tmp_path, field, value) -> None:
    config = make_config(tmp_path)
    with pytest.raises(ConfigValidationError):
        replace(config, **{field: value}).validate()


def test_dashboard_config_rejects_invalid_palette_and_ingest_env(tmp_path) -> None:
    config = make_config(tmp_path)
    with pytest.raises(ConfigValidationError):
        replace(config, palette=("red",)).validate()
    with pytest.raises(ConfigValidationError):
        replace(config, ingest_enabled=True, ingest_token_env="not an env").validate()


def test_dashboard_config_rejects_duplicate_metric_query_names(tmp_path) -> None:
    config = make_config(tmp_path)
    query = MetricQuery(name="cpu", query="up")
    with pytest.raises(ConfigValidationError):
        replace(config, metric_queries=(query, query)).validate()


# ---------------------------------------------------------------------------
# Reducer
# ---------------------------------------------------------------------------


def test_reducer_tracks_real_sessions_queries_and_cells(tmp_path) -> None:
    config = make_config(tmp_path)
    state = DashboardState(config, clock=_Clock())
    state.apply(
        make_event(
            "session.opened",
            data={
                "session_id": "s1",
                "user": "alice",
                "ip": "10.0.0.5",
                "services": {"orders": "read"},
                "adapter": "postgres-prod",
            },
        )
    )
    payload = state.to_dict()
    assert payload["summary"]["connected_users"] == 1
    assert payload["summary"]["total_services"] == 1
    assert payload["users"][0]["services"] == {"orders": "read"}
    assert payload["cells"][0]["state"] == "idle"
    assert payload["query_adapter"]["connected"] is True
    assert payload["query_adapter"]["adapter"] == "postgres-prod"

    state.apply(
        make_event(
            "query.started",
            data={
                "query_id": "q1",
                "session_id": "s1",
                "service": "orders",
                "adapter": "postgres-prod",
            },
        )
    )
    payload = state.to_dict()
    assert payload["cells"][0]["state"] == "active"
    assert payload["summary"]["active_queries"] == 1

    state.apply(
        make_event(
            "query.completed",
            data={
                "query_id": "q1",
                "session_id": "s1",
                "service": "orders",
                "adapter": "postgres-prod",
            },
        )
    )
    payload = state.to_dict()
    assert payload["cells"][0]["state"] == "idle"
    assert payload["summary"]["active_queries"] == 0
    assert payload["services"][0]["query_count"] == 1
    assert payload["services"][0]["request_rate"] > 0

    state.apply(
        make_event(
            "query.denied",
            data={
                "query_id": "q2",
                "session_id": "s1",
                "service": "orders",
                "adapter": "postgres-prod",
                "access": "none",
            },
        )
    )
    payload = state.to_dict()
    assert payload["services"][0]["denied_count"] == 1

    state.apply(
        make_event(
            "session.closed",
            data={"session_id": "s1", "adapter": "postgres-prod"},
        )
    )
    assert state.to_dict()["users"][0]["status"] == "closing"


def test_reducer_ignores_query_without_an_opened_session(tmp_path) -> None:
    config = make_config(tmp_path)
    state = DashboardState(config, clock=_Clock())
    state.apply(
        make_event(
            "query.started",
            data={"query_id": "q1", "session_id": "missing", "service": "orders"},
        )
    )
    payload = state.to_dict()
    assert payload["summary"]["active_queries"] == 0
    assert payload["cells"] == []


def test_reducer_tiles_incidents_approvals_and_deployments(tmp_path) -> None:
    config = make_config(tmp_path)
    state = DashboardState(config, clock=_Clock())
    state.apply(make_event("deployment.started", component="COMP-08", data={"app": "demo"}))
    tile = _tile(state, "COMP-08")
    assert tile["status"] == "active"
    state.apply(
        make_event(
            "deployment.completed",
            component="COMP-08",
            data={"app": "demo", "version": "1.2.0", "slot": "blue", "status": "success"},
        )
    )
    payload = state.to_dict()
    assert _tile(state, "COMP-08")["status"] == "healthy"
    assert payload["summary"]["last_deployment"]["version"] == "1.2.0"

    state.apply(
        make_event(
            "tool.failed",
            component="COMP-03",
            data={"tool": "configure_reverse_proxy", "status": "failed"},
            level="ERROR",
        )
    )
    assert _tile(state, "COMP-10")["status"] == "error"

    state.apply(
        make_event(
            "approval.requested",
            component="COMP-04",
            data={"request_id": "r1", "tool_name": "deploy_windows_service"},
        )
    )
    assert _tile(state, "COMP-04")["status"] == "pending"
    assert state.to_dict()["summary"]["pending_approvals"] == 1
    state.apply(
        make_event(
            "approval.granted",
            component="COMP-04",
            data={"request_id": "r1", "tool_name": "deploy_windows_service"},
        )
    )
    assert _tile(state, "COMP-04")["status"] == "healthy"
    assert state.to_dict()["summary"]["pending_approvals"] == 0

    state.apply(
        make_event(
            "incident.opened",
            component="COMP-07",
            data={"incident_id": "i1", "service": "demo"},
            level="ERROR",
        )
    )
    payload = state.to_dict()
    assert payload["summary"]["active_incidents"] == 1
    assert _tile(state, "COMP-07")["status"] == "error"
    state.apply(
        make_event(
            "incident.resolved",
            component="COMP-07",
            data={"incident_id": "i1", "service": "demo"},
        )
    )
    assert state.to_dict()["summary"]["active_incidents"] == 0


def test_reducer_control_metrics_report_and_trace(tmp_path) -> None:
    config = make_config(tmp_path)
    state = DashboardState(config, clock=_Clock())
    state.apply(make_event("control.started", component="COMP-01"))
    state.apply(
        make_event(
            "system.heartbeat",
            component="COMP-01",
            data={"uptime_seconds": 42.0, "service": "demo"},
        )
    )
    state.apply(
        make_event(
            "metrics.sample",
            component="COMP-06",
            data={
                "app": "demo",
                "host": "win-1",
                "cpu_percent": 12.5,
                "memory_mb": 256.0,
                "disk_percent": 40.0,
            },
        )
    )
    state.apply(
        make_event(
            "report.generated",
            component="COMP-01",
            data={
                "path": "report.md",
                "period_start": "2026-01-01T00:00:00Z",
                "period_end": "2026-01-08T00:00:00Z",
                "incidents": 1,
                "remediations": 1,
            },
        )
    )
    state.apply(
        make_event(
            "agent.decision",
            component="COMP-01",
            data={"decision": "remediate", "service": "demo"},
        )
    )
    payload = state.to_dict()
    assert payload["summary"]["control"]["running"] is True
    assert payload["summary"]["control"]["heartbeat_fresh"] is True
    assert payload["summary"]["control"]["uptime_seconds"] == 42.0
    assert payload["summary"]["latest_utilization"]["demo"]["cpu_percent"] == 12.5
    assert payload["summary"]["weekly_report"]["path"] == "report.md"
    assert state.trace()[-1]["kind"] == "DECISION"


def test_reducer_prunes_stale_queries_and_closed_sessions(tmp_path) -> None:
    config = make_config(tmp_path)
    clock = _Clock()
    state = DashboardState(config, clock=clock)
    state.apply(
        make_event(
            "session.opened",
            data={"session_id": "s1", "user": "alice", "services": ["orders"]},
        )
    )
    state.apply(
        make_event(
            "query.started",
            data={"query_id": "q1", "session_id": "s1", "service": "orders"},
        )
    )
    clock.advance(config.query_stale_seconds + 1)
    payload = state.to_dict()
    assert payload["summary"]["stale_queries"] == 1
    assert payload["summary"]["active_queries"] == 0

    state.apply(make_event("session.closed", data={"session_id": "s1"}))
    clock.advance(config.session_grace_seconds + 1)
    assert state.to_dict()["users"] == []


def test_reducer_deduplicates_events_and_buffers_tokens(tmp_path) -> None:
    config = make_config(tmp_path)
    state = DashboardState(config, clock=_Clock())
    event = make_event(
        "agent.token",
        component="COMP-01",
        data={"stream_id": "agent-1", "text": "hello "},
    )
    assert state.apply(event) is True
    assert state.apply(event) is False
    state.apply(
        make_event(
            "agent.token",
            component="COMP-01",
            data={"stream_id": "agent-1", "text": "world"},
        )
    )
    assert state.tokens(stream_id="agent-1")["text"] == {"agent-1": "hello world"}
    assert state.to_dict()["event_count"] == 2


def test_state_snapshot_round_trip(tmp_path) -> None:
    config = make_config(tmp_path)
    clock = _Clock()
    state = DashboardState(config, clock=clock)
    state.apply(
        make_event(
            "session.opened",
            data={
                "session_id": "s1",
                "user": "alice",
                "ip": "10.0.0.5",
                "services": {"orders": "read"},
                "adapter": "postgres-prod",
            },
        )
    )
    state.apply(make_event("deployment.started", component="COMP-08", data={"app": "demo"}))
    stored = state.to_store()
    restored = DashboardState(config, clock=clock)
    assert restored.restore(stored) is True
    before = state.to_dict()
    after = restored.to_dict()
    # Restored sessions are deliberately not marked "new" on a fresh page load.
    assert after["users"][0]["new"] is False
    for user in (before["users"][0], after["users"][0]):
        user.pop("new")
    assert after["users"] == before["users"]
    assert after["services"] == before["services"]
    assert after["summary"]["last_deployment"] == before["summary"]["last_deployment"]
    assert _tile(restored, "COMP-08")["status"] == "active"
    assert restored.restore({"version": 999}) is False


def test_snapshot_store_rejects_corrupt_or_negative_documents(tmp_path) -> None:
    store = SnapshotStore(tmp_path / "state.json")
    assert store.load() is None
    store.path.write_text("{not json", encoding="utf-8")
    assert store.load() is None
    store.save(journal_offset=17, state={"version": 1})
    snapshot = store.load()
    assert snapshot is not None
    assert snapshot.journal_offset == 17
    with pytest.raises(ValueError):
        store.save(journal_offset=-1, state={})
    store.clear()
    assert store.load() is None


# ---------------------------------------------------------------------------
# Service, ingest, API, SSE
# ---------------------------------------------------------------------------


def test_service_follows_journal_and_exposes_health(tmp_path) -> None:
    config = make_config(tmp_path)
    service = DashboardService(config, data_dir=tmp_path)
    service.ingest_session(
        {
            "event": "opened",
            "session_id": "s1",
            "user": "alice",
            "services": {"orders": "read"},
            "adapter": "postgres-prod",
        }
    )
    assert service.refresh() == 1
    assert service.state_payload()["summary"]["connected_users"] == 1
    health = service.health()
    assert health["status"] == "ok"
    assert health["service"] == "COMP-13"
    assert health["event_count"] == 1
    assert health["metrics_connected"] is False


def test_service_rejects_invalid_ingest_payloads(tmp_path) -> None:
    config = make_config(tmp_path)
    service = DashboardService(config, data_dir=tmp_path)
    with pytest.raises(ValueError):
        service.ingest_session({"event": "what", "session_id": "s1"})
    with pytest.raises(ValueError):
        service.ingest_session({"event": "opened", "session_id": "s1"})
    with pytest.raises(ValueError):
        service.ingest_query({"event": "started", "query_id": "q1"})


def test_service_snapshot_restores_users_after_restart(tmp_path) -> None:
    config = make_config(tmp_path)
    first = DashboardService(config, data_dir=tmp_path)
    first.ingest_session(
        {
            "event": "opened",
            "session_id": "s1",
            "user": "alice",
            "services": ["orders"],
            "adapter": "postgres-prod",
        }
    )
    first.refresh()
    first.save_snapshot()

    second = DashboardService(config, data_dir=tmp_path)

    async def lifecycle() -> dict:
        await second.start()
        try:
            return second.state_payload()
        finally:
            await second.stop()

    payload = asyncio.run(lifecycle())
    assert payload["summary"]["connected_users"] == 1
    assert payload["users"][0]["user"] == "alice"


def test_api_surfaces_and_static_frontend(tmp_path) -> None:
    config = make_config(tmp_path)
    service = DashboardService(config, data_dir=tmp_path)
    app = create_app(
        config, service=service, data_dir=tmp_path, frontend_dir=FRONTEND
    )
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["service"] == "COMP-13"

        state = client.get("/api/state")
        assert state.status_code == 200
        assert len(state.json()["tiles"]) == 8

        assert client.get("/api/summary").status_code == 200
        assert client.get("/api/events/recent?limit=10").status_code == 200
        assert client.get("/api/metrics?points=10").status_code == 200
        assert client.get("/api/logs").status_code == 200
        assert client.get("/api/trace").status_code == 200
        assert client.get("/api/tokens").status_code == 200
        assert client.get("/api/logs?level=LOUD").status_code == 422

        index = client.get("/")
        assert index.status_code == 200
        assert "OpsPilot" in index.text
        assert client.get("/mission-control/api/health").status_code == 200

        disabled = client.post(
            "/api/ingest/session",
            json={"event": "opened", "session_id": "s1", "user": "alice"},
        )
        assert disabled.status_code == 503
        assert "ADR-008" in disabled.json()["detail"]


def test_ingest_requires_bearer_token_and_updates_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPSPILOT_DASHBOARD_INGEST_TOKEN", "correct-token")
    config = make_config(tmp_path, ingest_enabled=True)
    service = DashboardService(config, data_dir=tmp_path)
    app = create_app(
        config, service=service, data_dir=tmp_path, frontend_dir=FRONTEND
    )
    payload = {
        "event": "opened",
        "session_id": "s1",
        "user": "alice",
        "ip": "10.0.0.5",
        "services": {"orders": "read"},
        "adapter": "postgres-prod",
    }
    with TestClient(app) as client:
        assert client.post("/api/ingest/session", json=payload).status_code == 401
        accepted = client.post(
            "/api/ingest/session",
            json=payload,
            headers={"Authorization": "Bearer correct-token"},
        )
        assert accepted.status_code == 202
        assert accepted.json()["type"] == "session.opened"

        query = client.post(
            "/api/ingest/query",
            json={
                "event": "started",
                "query_id": "q1",
                "session_id": "s1",
                "service": "orders",
                "adapter": "postgres-prod",
            },
            headers={"Authorization": "Bearer correct-token"},
        )
        assert query.status_code == 202
        service.refresh()

        state = client.get("/api/state").json()
        assert state["users"][0]["user"] == "alice"
        assert state["cells"][0]["state"] == "active"
        assert state["query_adapter"]["connected"] is True

        invalid = client.post(
            "/api/ingest/session",
            json={"event": "opened", "session_id": "s2"},
            headers={"Authorization": "Bearer correct-token"},
        )
        assert invalid.status_code == 422


def test_ingest_requires_configured_token_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPSPILOT_DASHBOARD_INGEST_TOKEN", raising=False)
    config = make_config(tmp_path, ingest_enabled=True)
    service = DashboardService(config, data_dir=tmp_path)
    app = create_app(
        config, service=service, data_dir=tmp_path, frontend_dir=FRONTEND
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/ingest/session",
            json={"event": "opened", "session_id": "s1", "user": "alice"},
            headers={"Authorization": "Bearer anything"},
        )
        assert response.status_code == 503
        assert "OPSPILOT_DASHBOARD_INGEST_TOKEN" in response.json()["detail"]


def test_sse_stream_emits_state_pulse_and_structural_update(tmp_path) -> None:
    config = make_config(tmp_path)
    service = DashboardService(config, data_dir=tmp_path)

    async def scenario() -> list[str]:
        stream = await service.stream(_FakeRequest())
        frames = [await anext(stream), await anext(stream)]
        service.publisher.publish(
            "tool.retry",
            component="COMP-03",
            message="retrying configure_reverse_proxy",
            data={"tool": "configure_reverse_proxy"},
        )
        service.refresh()
        frames.append(await anext(stream))
        service.publisher.publish(
            "deployment.completed",
            component="COMP-08",
            message="deployment completed",
            data={"app": "demo", "version": "1.0.0", "slot": "blue", "status": "success"},
        )
        service.refresh()
        frames.append(await anext(stream))
        await stream.aclose()
        return frames

    frames = asyncio.run(scenario())
    assert frames[0] == "retry: 2000\n\n"
    assert "event: state" in frames[1]
    assert '"tiles"' in frames[1]
    assert "event: pulse" in frames[2]
    assert '"tool.retry"' in frames[2]
    assert "event: update" in frames[3]
    assert '"state"' in frames[3]
    data_line = next(line for line in frames[3].splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["event"]["type"] == "deployment.completed"
    assert payload["state"]["summary"]["last_deployment"]["status"] == "success"


def test_frontend_assets_declare_real_surfaces_only() -> None:
    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    styles = (FRONTEND / "styles.css").read_text(encoding="utf-8")
    for element_id in (
        "tiles",
        "matrix",
        "users-body",
        "ticker",
        "tab-summary",
        "tab-observability",
        "tab-thinking",
        "frozen-banner",
        "adapter-badge",
    ):
        assert f'id="{element_id}"' in index
    assert 'new EventSource("api/events/stream")' in script
    assert "startPolling" in script
    assert "applyState" in script
    assert "Math.random" not in script
    assert "@keyframes" in styles
    assert ".pulse" in styles


class _Clock:
    def __init__(self) -> None:
        self._now = BASE_TIME

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


class _FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


def _tile(state: DashboardState, component: str) -> dict:
    return next(
        tile for tile in state.to_dict()["tiles"] if tile["component"] == component
    )
