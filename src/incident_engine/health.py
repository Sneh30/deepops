"""Health-check polling loop (COMP-07, FR-050/NFR-003)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from checkpoint_store.store import CheckpointStore
from common.clock import Clock, SystemClock
from common.log import StructuredLogger
from deployment_engine.health import HealthChecker
from deployment_engine.models import HealthTarget
from event_bus.publisher import EventPublisher, NullEventPublisher


@dataclass(frozen=True)
class HealthTargetSpec:
    name: str
    app: str
    service: str
    health: HealthTarget
    port: int | None = None
    failure_threshold: int = 3


@dataclass(frozen=True)
class PollResult:
    target: HealthTargetSpec
    healthy: bool
    error: str | None
    consecutive_failures: int
    triggered: bool = False
    details: dict[str, Any] = field(default_factory=dict)


class HealthPoller:
    def __init__(
        self,
        store: CheckpointStore,
        targets: list[HealthTargetSpec],
        checker: HealthChecker,
        *,
        clock: Clock | None = None,
        interval_seconds: int = 60,
        logger: StructuredLogger | None = None,
        events: EventPublisher | None = None,
    ) -> None:
        if interval_seconds < 1 or interval_seconds > 60:
            raise ValueError("polling interval must be between 1 and 60 seconds (NFR-003)")
        self._store = store
        self._targets = list(targets)
        self._checker = checker
        self._clock = clock or SystemClock()
        self._interval = interval_seconds
        self._logger = logger or StructuredLogger("COMP-07")
        self._events = events or NullEventPublisher()
        self._failures: dict[str, int] = {target.name: 0 for target in self._targets}
        self._alerted: dict[str, bool] = {target.name: False for target in self._targets}

    @property
    def interval_seconds(self) -> int:
        return self._interval

    def poll_once(self) -> list[PollResult]:
        results: list[PollResult] = []
        for target in self._targets:
            healthy, error = self._checker.check(target.health, port=target.port)
            if healthy:
                self._failures[target.name] = 0
                self._alerted[target.name] = False
            else:
                self._failures[target.name] += 1
            failures = self._failures[target.name]
            threshold = target.failure_threshold or target.health.failure_threshold
            triggered = False
            if not healthy and failures >= threshold and not self._alerted[target.name]:
                triggered = True
                self._alerted[target.name] = True
            self._store.record_health_sample(
                service=target.service, healthy=healthy, sampled_at=self._clock.now()
            )
            result = PollResult(
                target=target,
                healthy=healthy,
                error=error,
                consecutive_failures=failures,
                triggered=triggered,
                details={"checked_at": self._clock.now().isoformat()},
            )
            self._events.publish(
                "health.poll",
                component="COMP-07",
                message=(
                    f"health check passed: {target.service}"
                    if healthy
                    else f"health check failed: {target.service} ({error or 'unhealthy'})"
                ),
                data={
                    "target": target.name,
                    "app": target.app,
                    "service": target.service,
                    "healthy": healthy,
                    "error": error,
                    "consecutive_failures": failures,
                    "triggered": triggered,
                },
                level="INFO" if healthy else "WARN",
            )
            results.append(result)
        return results

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            for result in self.poll_once():
                if result.triggered:
                    self._logger.warning(
                        "health threshold crossed",
                        target=result.target.name,
                        service=result.target.service,
                        failures=result.consecutive_failures,
                        error=result.error,
                    )
            stop_event.wait(self._interval)

    def reset(self, name: str) -> None:
        self._failures[name] = 0
        self._alerted[name] = False
