"""Post-deploy health verification (COMP-08, FR-013/NFR-006)."""

from __future__ import annotations

import socket
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import psutil

from common.clock import Clock, SystemClock
from deployment_engine.models import HealthTarget
from tools_readonly.health import HttpProber, HttpProbeResult, UrllibProber


@dataclass(frozen=True)
class HealthOutcome:
    healthy: bool
    attempts: int
    consecutive_successes: int
    failures: int
    elapsed_seconds: float
    last_error: str | None = None
    samples: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "attempts": self.attempts,
            "consecutive_successes": self.consecutive_successes,
            "failures": self.failures,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "last_error": self.last_error,
        }


class HealthChecker:
    def __init__(
        self,
        *,
        prober: HttpProber | None = None,
        tcp_connector: Callable[[str, int, float], None] | None = None,
        process_checker: Callable[[str], bool] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._prober = prober or UrllibProber()
        self._tcp_connector = tcp_connector or _tcp_connect
        self._process_checker = process_checker or _process_running
        self._clock = clock or SystemClock()

    def check(self, target: HealthTarget, *, port: int | None = None) -> tuple[bool, str | None]:
        if target.kind == "http":
            if not target.url:
                return False, "health check has no URL"
            url = _replace_port(target.url, port) if port else target.url
            result: HttpProbeResult = self._prober.probe(url, target.timeout_seconds)
            return result.healthy, result.error
        if target.kind == "tcp":
            host = target.host or "127.0.0.1"
            target_port = port or target.port
            if target_port is None:
                return False, "TCP health check has no port"
            try:
                self._tcp_connector(host, target_port, float(target.timeout_seconds))
                return True, None
            except OSError as error:
                return False, str(error)
        if target.kind == "process":
            if not target.process_name:
                return False, "process health check has no process_name"
            try:
                healthy = self._process_checker(target.process_name)
                return healthy, None if healthy else "process is not running"
            except (psutil.Error, OSError) as error:
                return False, str(error)
        return False, f"unsupported health-check type: {target.kind}"

    def wait(
        self,
        target: HealthTarget,
        *,
        port: int | None = None,
        window_seconds: int,
    ) -> HealthOutcome:
        started = self._clock.monotonic()
        deadline = started + window_seconds
        attempts = 0
        successes = 0
        failures = 0
        last_error: str | None = None
        samples: list[dict[str, Any]] = []
        while True:
            attempts += 1
            healthy, error = self.check(target, port=port)
            samples.append(
                {
                    "attempt": attempts,
                    "healthy": healthy,
                    "error": error,
                    "at_seconds": round(self._clock.monotonic() - started, 2),
                }
            )
            if healthy:
                successes += 1
                failures = 0
            else:
                failures += 1
                successes = 0
                last_error = error
            if successes >= target.success_threshold:
                return HealthOutcome(
                    healthy=True,
                    attempts=attempts,
                    consecutive_successes=successes,
                    failures=failures,
                    elapsed_seconds=self._clock.monotonic() - started,
                    samples=tuple(samples),
                )
            if failures >= target.failure_threshold:
                break
            if self._clock.monotonic() + target.interval_seconds > deadline:
                break
            self._clock.sleep(target.interval_seconds)
        return HealthOutcome(
            healthy=False,
            attempts=attempts,
            consecutive_successes=successes,
            failures=failures,
            elapsed_seconds=self._clock.monotonic() - started,
            last_error=last_error,
            samples=tuple(samples),
        )


def _replace_port(url: str, port: int) -> str:
    parts = urllib.parse.urlsplit(url)
    hostname = parts.hostname or "127.0.0.1"
    netloc = f"{hostname}:{port}"
    return urllib.parse.urlunsplit(
        (parts.scheme, netloc, parts.path or "/", parts.query, parts.fragment)
    )


def _tcp_connect(host: str, port: int, timeout: float) -> None:
    with socket.create_connection((host, port), timeout=timeout):
        return None


def _process_running(name: str) -> bool:
    lowered = name.lower()
    for process in psutil.process_iter(["name"]):
        try:
            if (process.info.get("name") or "").lower() == lowered:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False
