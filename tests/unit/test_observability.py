from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from observability.logs import LogAggregator
from observability.utilization import UtilizationRecorder, UtilizationSample


def event(level: str, component: str, message: str, when: datetime) -> str:
    return json.dumps(
        {
            "timestamp": when.isoformat(),
            "level": level,
            "component": component,
            "message": message,
            "context": {"key": "value"},
        }
    )


def write_log(path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_aggregator_parses_and_filters(tmp_path) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    path = tmp_path / "app.jsonl"
    write_log(
        path,
        [
            event("INFO", "COMP-01", "started", now),
            event("ERROR", "COMP-08", "deploy failed", now + timedelta(minutes=1)),
            event("INFO", "COMP-08", "deploy ok", now + timedelta(minutes=2)),
        ],
    )
    aggregator = LogAggregator()
    errors = aggregator.query([path], level="ERROR")
    assert len(errors) == 1
    assert errors[0].component == "COMP-08"
    assert aggregator.errors == []
    summary = aggregator.summarize([path])
    assert summary.total == 3
    assert summary.error_count == 1
    assert summary.by_component == {"COMP-01": 1, "COMP-08": 2}
    assert summary.first_timestamp == now


def test_aggregator_records_malformed_lines(tmp_path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    aggregator = LogAggregator()
    assert list(aggregator.iter_events([path])) == []
    assert len(aggregator.errors) == 1


def test_aggregator_detects_missing_fields(tmp_path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps({"timestamp": "2026-01-01T00:00:00Z"}) + "\n", encoding="utf-8")
    aggregator = LogAggregator()
    assert list(aggregator.iter_events([path])) == []
    assert "missing fields" in aggregator.errors[0]


def test_aggregator_limit_returns_most_recent(tmp_path) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    path = tmp_path / "app.jsonl"
    write_log(
        path,
        [event("INFO", "C", f"m{index}", now + timedelta(seconds=index)) for index in range(5)],
    )
    aggregator = LogAggregator()
    matches = aggregator.query([path], limit=2)
    assert [item.message for item in matches] == ["m3", "m4"]


class FakeProvider:
    def __init__(self, value: float) -> None:
        self.value = value

    def sample(self, app: str) -> UtilizationSample:
        return UtilizationSample("host", app, self.value, self.value * 10, self.value / 2)


def test_utilization_recorder_persists_samples(store, clock) -> None:
    recorder = UtilizationRecorder(
        store, FakeProvider(25.0), ["app"], clock=clock, interval_seconds=60
    )
    samples = recorder.run_once()
    assert samples[0].cpu_percent == 25.0
    rollup = store.utilization_rollup(
        start=clock.now() - timedelta(minutes=1), end=clock.now() + timedelta(minutes=1), app="app"
    )
    assert rollup.count == 1
    assert rollup.cpu_avg == 25.0


def test_utilization_interval_is_bounded(store, clock) -> None:
    import pytest

    for interval in (1, 7200):
        with pytest.raises(ValueError):
            UtilizationRecorder(
                store, FakeProvider(1.0), ["app"], clock=clock, interval_seconds=interval
            )
