"""Alert-rule evaluation for COMP-07 (FR-042, ADR-005).

Rules are evaluated in-process against metric snapshots; no Alertmanager
dependency is introduced for the MVP. A rule only fires after it has been
breaching continuously for its configured ``for_seconds``.
"""

from __future__ import annotations

import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from common.clock import Clock, SystemClock
from common.config import load_config
from common.errors import PreconditionFailed

OPERATORS: dict[str, Callable[[float, float], bool]] = {
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "eq": operator.eq,
    "neq": operator.ne,
}


@dataclass(frozen=True)
class MetricSnapshot:
    values: Mapping[str, float]
    captured_at: datetime | None = None
    host: str | None = None
    app: str | None = None


@dataclass(frozen=True)
class AlertRule:
    name: str
    severity: str
    metric: str
    operator: str
    threshold: float
    for_seconds: int
    description: str = ""

    def __post_init__(self) -> None:
        if self.operator not in OPERATORS:
            raise PreconditionFailed(f"unknown alert operator: {self.operator}")
        if self.severity not in {"warning", "critical"}:
            raise PreconditionFailed(f"unknown alert severity: {self.severity}")

    def matches(self, value: float | None) -> bool:
        if value is None:
            return False
        return OPERATORS[self.operator](float(value), float(self.threshold))


@dataclass(frozen=True)
class Alert:
    name: str
    severity: str
    metric: str
    value: float
    threshold: float
    active_seconds: float
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "severity": self.severity,
            "metric": self.metric,
            "value": self.value,
            "threshold": self.threshold,
            "active_seconds": round(self.active_seconds, 2),
            "description": self.description,
        }


@dataclass
class _RuleState:
    since: float | None = None
    fired: bool = False


class AlertEvaluator:
    def __init__(
        self, rules: list[AlertRule], *, clock: Clock | None = None
    ) -> None:
        self._rules = list(rules)
        self._clock = clock or SystemClock()
        self._states: dict[str, _RuleState] = {
            rule.name: _RuleState() for rule in self._rules
        }

    @classmethod
    def from_config(cls, path: str | Path, *, clock: Clock | None = None) -> AlertEvaluator:
        document = load_config(path, "alert-rules.schema.json")
        rules = [
            AlertRule(
                name=str(item["name"]),
                severity=str(item["severity"]),
                metric=str(item["metric"]),
                operator=str(item["operator"]),
                threshold=float(item["threshold"]),
                for_seconds=int(item["for_seconds"]),
                description=str(item.get("description", "")),
            )
            for item in document["rules"]
        ]
        return cls(rules, clock=clock)

    @property
    def rules(self) -> tuple[AlertRule, ...]:
        return tuple(self._rules)

    def evaluate(self, snapshot: MetricSnapshot) -> list[Alert]:
        now = self._clock.monotonic()
        firing: list[Alert] = []
        for rule in self._rules:
            state = self._states[rule.name]
            value = snapshot.values.get(rule.metric)
            if not rule.matches(value):
                state.since = None
                state.fired = False
                continue
            if state.since is None:
                state.since = now
            active = now - state.since
            if active >= rule.for_seconds and not state.fired:
                state.fired = True
                firing.append(
                    Alert(
                        name=rule.name,
                        severity=rule.severity,
                        metric=rule.metric,
                        value=float(value),  # type: ignore[arg-type]
                        threshold=rule.threshold,
                        active_seconds=active,
                        description=rule.description,
                    )
                )
        return firing

    def active_alerts(self, snapshot: MetricSnapshot) -> list[dict[str, Any]]:
        now = self._clock.monotonic()
        active: list[dict[str, Any]] = []
        for rule in self._rules:
            value = snapshot.values.get(rule.metric)
            if rule.matches(value):
                state = self._states[rule.name]
                active.append(
                    {
                        "name": rule.name,
                        "severity": rule.severity,
                        "metric": rule.metric,
                        "value": float(value),  # type: ignore[arg-type]
                        "threshold": rule.threshold,
                        "active_seconds": round(now - state.since, 2)
                        if state.since is not None
                        else 0.0,
                        "description": rule.description,
                    }
                )
        return active
