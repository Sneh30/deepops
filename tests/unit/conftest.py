from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from approval_gate.gate import ApprovalGate
from approval_gate.policy import ApprovalPolicy
from approval_gate.store import SqliteApprovalStore
from audit_log.writer import AuditLog
from checkpoint_store.store import CheckpointStore
from common.clock import Clock
from common.process import CommandResult


class FakeClock(Clock):
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        self._monotonic = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._monotonic += seconds
        self._now += timedelta(seconds=seconds)

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        self._monotonic += seconds


@dataclass
class FakePowerShellRunner:
    responses: list[Any] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)

    def run_json(self, script: str, *, timeout_seconds: float = 120, cwd: Any = None) -> Any:
        self.calls.append("run_json")
        self.scripts.append(script)
        if not self.responses:
            raise AssertionError("FakePowerShellRunner has no queued response")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def run_script(self, script: str, *, timeout_seconds: float = 120, cwd: Any = None) -> CommandResult:
        self.calls.append("run_script")
        self.scripts.append(script)
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
        return CommandResult(args=("pwsh",), returncode=0, stdout="{}", stderr="")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(tmp_path, clock) -> CheckpointStore:
    return CheckpointStore(tmp_path / "state.db", clock=clock)


@pytest.fixture
def audit(tmp_path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


@pytest.fixture
def policy() -> ApprovalPolicy:
    return ApprovalPolicy.from_document(
        {
            "version": "1",
            "approval_required_categories": [
                "destructive",
                "irreversible",
                "externally_visible",
                "security_sensitive",
            ],
            "request_ttl_seconds": 3600,
            "require_rollback_plan": True,
        }
    )


@pytest.fixture
def approval_store(tmp_path, clock) -> SqliteApprovalStore:
    return SqliteApprovalStore(tmp_path / "state.db", clock=clock)


@pytest.fixture
def gate(policy, approval_store) -> ApprovalGate:
    return ApprovalGate(policy, approval_store)


@pytest.fixture
def fake_runner_factory():
    def factory(responses: list[Any]) -> FakePowerShellRunner:
        return FakePowerShellRunner(list(responses))

    return factory
