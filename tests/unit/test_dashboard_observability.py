"""Prometheus feed and log feed tests for Mission Control (COMP-06/COMP-13)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from dashboard_ui.logs import LogFeed
from event_bus.model import Event
from observability.prometheus import (
    MetricSample,
    MetricStore,
    ObservabilityLoop,
    PrometheusClient,
    PrometheusError,
)


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def publish(
        self,
        event_type: str,
        *,
        component: str,
        message: str,
        data=None,
        level: str = "INFO",
        timestamp: datetime | None = None,
    ) -> Event:
        event = Event.create(
            event_type=event_type,
            component=component,
            message=message,
            data=data or {},
            level=level,
            timestamp=timestamp,
        )
        self.events.append(event)
        return event


class FakeClient:
    def __init__(self, *, error: str | None = None, value: float = 1.0) -> None:
        self.error = error
        self.value = value
        self.queries: list[str] = []

    def query(self, expression: str) -> list[MetricSample]:
        self.queries.append(expression)
        if self.error is not None:
            raise PrometheusError(self.error)
        return [
            MetricSample(
                name="",
                labels={"instance": "win-1"},
                value=self.value,
                timestamp=1_700_000_000.0,
            )
        ]


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> bool:
        return False


def test_prometheus_client_parses_samples(monkeypatch) -> None:
    payload = {
        "status": "success",
        "data": {
            "result": [
                {
                    "metric": {"instance": "win-1", "job": "windows"},
                    "value": [1_700_000_000.0, "12.5"],
                },
                {"metric": {}, "value": [1_700_000_000.0, "NaN"]},
                {"metric": {}, "value": ["bad"]},
            ]
        },
    }
    monkeypatch.setattr(
        "observability.prometheus.urllib.request.urlopen",
        lambda request, timeout: FakeResponse(payload),
    )
    client = PrometheusClient("http://prometheus:9090/", timeout_seconds=3)
    samples = client.query("up")
    assert len(samples) == 1
    assert samples[0].value == 12.5
    assert samples[0].labels == {"instance": "win-1", "job": "windows"}
    with pytest.raises(ValueError):
        client.query("   ")
    with pytest.raises(ValueError):
        PrometheusClient("prometheus:9090")


def test_prometheus_client_rejects_failed_responses(monkeypatch) -> None:
    monkeypatch.setattr(
        "observability.prometheus.urllib.request.urlopen",
        lambda request, timeout: FakeResponse({"status": "error"}),
    )
    client = PrometheusClient("http://prometheus:9090")
    with pytest.raises(PrometheusError):
        client.query("up")


def test_metric_store_bounds_series_and_tracks_connection() -> None:
    store = MetricStore(max_points=3)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    samples = [
        MetricSample(name="", labels={"i": "1"}, value=float(index), timestamp=float(index))
        for index in range(5)
    ]
    assert store.record("cpu", samples, now=now) == 5
    store.mark_success(now=now)
    snapshot = store.snapshot(points=10)
    assert snapshot["connected"] is True
    assert snapshot["scrape_count"] == 1
    assert len(snapshot["series"]) == 1
    assert [point[1] for point in snapshot["series"][0]["points"]] == [2.0, 3.0, 4.0]

    store.mark_failure(now=now, error="boom")
    snapshot = store.snapshot()
    assert snapshot["connected"] is False
    assert snapshot["last_error"] == "boom"
    assert snapshot["failure_count"] == 1


def test_observability_loop_records_scrapes_and_events() -> None:
    publisher = RecordingPublisher()
    store = MetricStore()
    loop = ObservabilityLoop(
        FakeClient(value=42.0),
        store,
        {"cpu_percent": "up", "memory": "up"},
        publisher=publisher,
        interval_seconds=15,
    )
    result = loop.scrape_once()
    assert result["ok"] is True
    assert result["recorded"] == 2
    assert store.connected is True
    snapshot = store.snapshot()
    assert {series["name"] for series in snapshot["series"]} == {
        "cpu_percent",
        "memory",
    }
    assert [event.type for event in publisher.events] == ["metrics.scrape"]
    assert publisher.events[0].data["samples"] == 2


def test_observability_loop_surfaces_total_failure() -> None:
    publisher = RecordingPublisher()
    store = MetricStore()
    loop = ObservabilityLoop(
        FakeClient(error="connection refused"),
        store,
        {"cpu_percent": "up"},
        publisher=publisher,
        interval_seconds=15,
    )
    result = loop.scrape_once()
    assert result["ok"] is False
    assert store.connected is False
    assert "connection refused" in str(store.last_error)
    assert [event.type for event in publisher.events] == ["metrics.scrape_failed"]
    assert publisher.events[0].level == "ERROR"


def test_observability_loop_partial_failure_stays_connected() -> None:
    class PartialClient:
        def query(self, expression: str) -> list[MetricSample]:
            if expression == "bad":
                raise PrometheusError("bad query")
            return [
                MetricSample(name="", labels={}, value=1.0, timestamp=1_700_000_000.0)
            ]

    publisher = RecordingPublisher()
    store = MetricStore()
    loop = ObservabilityLoop(
        PartialClient(),
        store,
        {"good": "up", "broken": "bad"},
        publisher=publisher,
        interval_seconds=15,
    )
    result = loop.scrape_once()
    assert result["ok"] is True
    assert result["recorded"] == 1
    assert result["errors"]
    assert store.connected is True
    assert [event.type for event in publisher.events] == ["metrics.scrape"]
    assert publisher.events[0].level == "WARN"


def test_observability_interval_is_bounded() -> None:
    store = MetricStore()
    with pytest.raises(ValueError):
        ObservabilityLoop(FakeClient(), store, {}, interval_seconds=1)


def test_log_feed_merges_jsonl_and_audit_entries(tmp_path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def log_line(level: str, component: str, message: str, when: datetime) -> str:
        return json.dumps(
            {
                "timestamp": when.isoformat(),
                "level": level,
                "component": component,
                "message": message,
                "context": {"key": "value"},
            }
        )

    (log_dir / "control.jsonl").write_text(
        "\n".join(
            [
                log_line("INFO", "COMP-01", "started", base),
                log_line("ERROR", "COMP-08", "deploy failed", base + timedelta(minutes=1)),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (log_dir / "events.jsonl").write_text(
        log_line("INFO", "COMP-13", "not a log file", base) + "\n", encoding="utf-8"
    )
    (log_dir / "audit.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-01-01T12:02:00Z",
                "action": "deploy_windows_service",
                "inputs": {},
                "outcome": "failed",
                "checkpoint_id": "ck-1",
                "approval_ref": "ap-1",
                "evidence": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    feed = LogFeed(log_dir, audit_path=log_dir / "audit.jsonl", limit=10)
    payload = feed.query()
    assert payload["path_count"] == 1
    assert len(payload["events"]) == 3
    assert {event["component"] for event in payload["events"]} == {
        "COMP-01",
        "COMP-08",
        "COMP-12",
    }

    errors = feed.query(level="ERROR")
    assert errors["events"]
    assert all(event["level"] == "ERROR" for event in errors["events"])
    assert any(event["component"] == "COMP-12" for event in errors["events"])

    audit_only = feed.query(component="COMP-12")
    assert len(audit_only["events"]) == 1
    assert audit_only["events"][0]["context"]["source"] == "audit"

    summary = feed.summarize()
    assert summary["total"] == 2
    assert summary["audit_entries"] == 1

    with pytest.raises(ValueError):
        feed.query(level="LOUD")
