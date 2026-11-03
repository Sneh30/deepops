"""Resource-utilization sampling (COMP-06, FR-082)."""

from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import psutil

from checkpoint_store.store import CheckpointStore
from common.clock import Clock, SystemClock
from common.log import StructuredLogger
from event_bus.publisher import EventPublisher, NullEventPublisher


@dataclass(frozen=True)
class UtilizationSample:
    host: str
    app: str
    cpu_percent: float
    memory_mb: float
    disk_percent: float


class UtilizationProvider(Protocol):
    def sample(self, app: str) -> UtilizationSample: ...


class PsutilUtilizationProvider:
    def __init__(self, *, host: str, disk_path: str | Path, process_names: dict[str, str] | None = None) -> None:
        self.host = host
        self.disk_path = Path(disk_path)
        self.process_names = {key.lower(): value for key, value in (process_names or {}).items()}

    def sample(self, app: str) -> UtilizationSample:
        name = self.process_names.get(app.lower(), app)
        memory_mb = 0.0
        process_cpu = 0.0
        found = False
        for process in psutil.process_iter(["name"]):
            try:
                if (process.info.get("name") or "").lower() != name.lower():
                    continue
                found = True
                memory_mb += process.memory_info().rss / (1024 * 1024)
                process_cpu += process.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        usage = shutil.disk_usage(self.disk_path)
        disk_percent = (usage.used / usage.total) * 100 if usage.total else 0.0
        return UtilizationSample(
            host=self.host,
            app=app,
            cpu_percent=round(process_cpu if found else 0.0, 2),
            memory_mb=round(memory_mb, 2),
            disk_percent=round(disk_percent, 2),
        )


class UtilizationRecorder:
    def __init__(
        self,
        store: CheckpointStore,
        provider: UtilizationProvider,
        apps: list[str],
        *,
        clock: Clock | None = None,
        interval_seconds: int = 60,
        logger: StructuredLogger | None = None,
        events: EventPublisher | None = None,
    ) -> None:
        if interval_seconds < 10 or interval_seconds > 3600:
            raise ValueError("utilization interval must be between 10 and 3600 seconds")
        self._store = store
        self._provider = provider
        self._apps = list(apps)
        self._clock = clock or SystemClock()
        self._interval = interval_seconds
        self._logger = logger or StructuredLogger("COMP-06")
        self._events = events or NullEventPublisher()

    def run_once(self) -> list[UtilizationSample]:
        samples: list[UtilizationSample] = []
        for app in self._apps:
            sample = self._provider.sample(app)
            sampled_at = self._clock.now()
            self._store.record_utilization(
                host=sample.host,
                app=sample.app,
                cpu_percent=sample.cpu_percent,
                memory_mb=sample.memory_mb,
                disk_percent=sample.disk_percent,
                sampled_at=sampled_at,
            )
            self._events.publish(
                "metrics.sample",
                component="COMP-06",
                message=(
                    f"utilization {app}: CPU {sample.cpu_percent:.1f}% · "
                    f"MEM {sample.memory_mb:.0f} MB · DISK {sample.disk_percent:.1f}%"
                ),
                data={
                    "host": sample.host,
                    "app": sample.app,
                    "cpu_percent": sample.cpu_percent,
                    "memory_mb": sample.memory_mb,
                    "disk_percent": sample.disk_percent,
                },
                timestamp=sampled_at,
            )
            samples.append(sample)
        return samples

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self.run_once()
            except Exception as error:
                self._logger.exception("utilization sample failed", error)
            stop_event.wait(self._interval)
