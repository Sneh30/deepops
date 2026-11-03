"""Prometheus-backed observability feed for COMP-13 (UI_SPEC.md §5.2).

The dashboard queries Prometheus through its HTTP API with the standard
library only; no additional runtime dependency is introduced. A failed scrape
is surfaced as a real ``metrics.scrape_failed`` event rather than silently
leaving stale charts looking live.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from common.clock import Clock, SystemClock
from event_bus.model import Event
from event_bus.publisher import EventPublisher, NullEventPublisher

_MAX_POINTS = 720


class PrometheusError(RuntimeError):
    pass


@dataclass(frozen=True)
class MetricSample:
    name: str
    labels: dict[str, str]
    value: float
    timestamp: float


class PrometheusClient:
    def __init__(self, base_url: str, *, timeout_seconds: int = 5) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("Prometheus base URL must be http(s)")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def query(self, expression: str) -> list[MetricSample]:
        if not expression.strip():
            raise ValueError("Prometheus query must not be empty")
        parameters = urllib.parse.urlencode({"query": expression})
        url = f"{self.base_url}/api/v1/query?{parameters}"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise PrometheusError(f"Prometheus query failed: {error}") from error
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise PrometheusError("Prometheus returned a non-success response")
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("result"), list):
            raise PrometheusError("Prometheus response has no result list")
        samples: list[MetricSample] = []
        for item in data["result"]:
            if not isinstance(item, dict):
                continue
            metric = item.get("metric", {})
            value = item.get("value")
            if not isinstance(metric, Mapping) or not isinstance(value, list) or len(value) != 2:
                continue
            try:
                numeric = float(value[1])
                timestamp = float(value[0])
            except (TypeError, ValueError):
                continue
            if numeric != numeric or numeric in {float("inf"), float("-inf")}:
                continue
            samples.append(
                MetricSample(
                    name="",
                    labels={str(key): str(val) for key, val in metric.items()},
                    value=numeric,
                    timestamp=timestamp,
                )
            )
        return samples


class MetricStore:
    """Bounded in-memory time series, updated only by real Prometheus scrapes."""

    def __init__(self, *, max_points: int = _MAX_POINTS) -> None:
        self._lock = threading.RLock()
        self._series: dict[str, dict[str, Any]] = {}
        self._max_points = max_points
        self.connected = False
        self.last_scrape_at: datetime | None = None
        self.last_error: str | None = None
        self.scrape_count = 0
        self.failure_count = 0

    def record(
        self,
        name: str,
        samples: list[MetricSample],
        *,
        now: datetime,
    ) -> int:
        with self._lock:
            recorded = 0
            for sample in samples:
                labels = dict(sample.labels)
                key = json.dumps([name, sorted(labels.items())], separators=(",", ":"))
                series = self._series.get(key)
                if series is None:
                    series = {
                        "name": name,
                        "labels": labels,
                        "points": deque(maxlen=self._max_points),
                    }
                    self._series[key] = series
                series["points"].append((sample.timestamp, sample.value))
                recorded += 1
            return recorded

    def mark_success(self, *, now: datetime) -> None:
        with self._lock:
            self.connected = True
            self.last_scrape_at = now
            self.last_error = None
            self.scrape_count += 1

    def mark_failure(self, *, now: datetime, error: str) -> None:
        with self._lock:
            self.connected = False
            self.last_scrape_at = now
            self.last_error = error
            self.failure_count += 1

    def snapshot(self, *, points: int = 120) -> dict[str, Any]:
        with self._lock:
            series = []
            for item in self._series.values():
                series.append(
                    {
                        "name": item["name"],
                        "labels": item["labels"],
                        "points": [
                            [timestamp, value]
                            for timestamp, value in list(item["points"])[-points:]
                        ],
                    }
                )
            series.sort(key=lambda item: (item["name"], json.dumps(item["labels"], sort_keys=True)))
            return {
                "connected": self.connected,
                "last_scrape_at": self.last_scrape_at.isoformat()
                if self.last_scrape_at
                else None,
                "last_error": self.last_error,
                "scrape_count": self.scrape_count,
                "failure_count": self.failure_count,
                "series": series,
            }


class ObservabilityLoop:
    def __init__(
        self,
        client: PrometheusClient,
        store: MetricStore,
        queries: Mapping[str, str],
        *,
        publisher: EventPublisher | None = None,
        interval_seconds: int = 15,
        clock: Clock | None = None,
    ) -> None:
        if interval_seconds < 5:
            raise ValueError("observability interval must be at least 5 seconds")
        self._client = client
        self._store = store
        self._queries = dict(queries)
        self._publisher = publisher or NullEventPublisher()
        self._interval = interval_seconds
        self._clock = clock or SystemClock()

    def scrape_once(self) -> dict[str, Any]:
        started = time.monotonic()
        now = self._clock.now()
        errors: list[str] = []
        recorded = 0
        for name, expression in self._queries.items():
            try:
                samples = self._client.query(expression)
            except (PrometheusError, ValueError) as error:
                errors.append(f"{name}: {error}")
                continue
            recorded += self._store.record(name, samples, now=now)
        duration_ms = int((time.monotonic() - started) * 1000)
        if errors and len(errors) == len(self._queries):
            message = "; ".join(errors)[:500]
            self._store.mark_failure(now=now, error=message)
            self._publisher.publish(
                "metrics.scrape_failed",
                component="COMP-06",
                message=f"Prometheus scrape failed: {message}",
                data={"errors": errors[:10], "duration_ms": duration_ms},
                level="ERROR",
                timestamp=now,
            )
            return {
                "ok": False,
                "recorded": recorded,
                "errors": errors,
                "duration_ms": duration_ms,
            }
        self._store.mark_success(now=now)
        self._publisher.publish(
            "metrics.scrape",
            component="COMP-06",
            message=(
                f"Prometheus scrape completed: {recorded} sample(s)"
                + (f", {len(errors)} query error(s)" if errors else "")
            ),
            data={
                "samples": recorded,
                "queries": len(self._queries),
                "errors": errors[:10],
                "duration_ms": duration_ms,
            },
            level="WARN" if errors else "INFO",
            timestamp=now,
        )
        return {
            "ok": True,
            "recorded": recorded,
            "errors": errors,
            "duration_ms": duration_ms,
        }

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if self._queries:
                try:
                    await asyncio.to_thread(self.scrape_once)
                except Exception as error:  # defensive: never take the UI down
                    self._store.mark_failure(
                        now=self._clock.now(), error=f"{type(error).__name__}: {error}"
                    )
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval)
            except TimeoutError:
                continue


def sample_event(event: Event) -> bool:
    """Small helper used by tests to assert a scrape event shape."""
    return event.type in {"metrics.scrape", "metrics.scrape_failed"}
