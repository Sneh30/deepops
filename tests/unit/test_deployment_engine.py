from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from checkpoint_store.store import CheckpointStore
from common.contracts import ToolContext, ToolResult
from common.errors import ApprovalRequired
from deployment_engine.engine import DeploymentEngine
from deployment_engine.health import HealthOutcome
from deployment_engine.models import DeploymentConfig, HealthTarget, RolloutConfig


def make_config(**overrides: Any) -> DeploymentConfig:
    values: dict[str, Any] = {
        "name": "example-app",
        "package_type": "windows_service",
        "package_path": r"C:\OpsPilot\packages\example-app",
        "version": "1.4.2",
        "environment": "production",
        "ports": (8080,),
        "health_check": HealthTarget(
            kind="http",
            url="http://127.0.0.1:8080/health",
            interval_seconds=1,
            timeout_seconds=1,
            success_threshold=1,
            failure_threshold=2,
        ),
        "rollout": RolloutConfig(
            strategy="blue_green",
            verification_window_seconds=60,
            stability_window_seconds=300,
            max_failed_health_checks=3,
        ),
        "service_account": r"OPSPILOT\svc-example",
        "executable": "example-app.exe",
        "arguments": ("--port", "{port}"),
        "env": {},
    }
    values.update(overrides)
    return DeploymentConfig(**values)


class FakeHealthChecker:
    def __init__(self, healthy: list[bool]) -> None:
        self.results = list(healthy)
        self.calls: list[dict[str, Any]] = []

    def wait(self, target: HealthTarget, *, port: int | None, window_seconds: int) -> HealthOutcome:
        self.calls.append({"port": port, "window": window_seconds})
        healthy = self.results.pop(0)
        return HealthOutcome(
            healthy=healthy,
            attempts=1,
            consecutive_successes=1 if healthy else 0,
            failures=0 if healthy else 1,
            elapsed_seconds=1.0,
        )

    def check(self, target: HealthTarget, *, port: int | None = None) -> tuple[bool, str | None]:
        return True, None


class FakeTool:
    def __init__(self) -> None:
        self.restores = 0

    def capture_state(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {"value": 1}

    def restore(self, state: Mapping[str, Any]) -> None:
        self.restores += 1


class FakeRegistry:
    def __init__(self, tool: FakeTool, responses: list[Any]) -> None:
        self.tool = tool
        self.responses = list(responses)
        self.calls: list[tuple[str, Mapping[str, Any], ToolContext | None, str]] = []

    def execute(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        context: ToolContext | None = None,
        rollback_plan: str = "",
    ) -> ToolResult:
        self.calls.append((name, payload, context, rollback_plan))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def get(self, name: str) -> FakeTool:
        assert name in {"deploy_windows_service", "deploy_windows_container"}
        return self.tool


class FakeCutover:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.ports: list[int] = []

    def cutover(self, *, port: int, context: ToolContext, rollback_plan: str) -> ToolResult:
        self.ports.append(port)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_successful_deploy_uses_green_slot_and_records_active(store: CheckpointStore) -> None:
    tool = FakeTool()
    registry = FakeRegistry(
        tool,
        [
            ToolResult(
                status="success",
                output={"service_name": "example-app-green"},
                checkpoint_id="ckpt-candidate",
            )
        ],
    )
    health = FakeHealthChecker([True, True])
    engine = DeploymentEngine(registry, store, health)  # type: ignore[arg-type]
    result = engine.deploy(make_config())
    assert result.status == "success"
    assert result.active_slot == "green"
    assert result.previous_slot is None
    assert registry.calls[0][1]["service_name"] == "example-app-green"
    assert registry.calls[0][1]["arguments"] == ["--port", "8081"]
    records = store.list_deployments("example-app")
    assert records[0]["status"] == "success"
    assert records[0]["details"]["active"] is True
    assert engine.active_slot("example-app") == "green"


def test_failed_pre_cutover_health_rolls_back_candidate(store: CheckpointStore) -> None:
    tool = FakeTool()
    checkpoint_id = store.snapshot(
        "deploy",
        tool.capture_state({}),
        metadata={"tool": "deploy_windows_service", "payload": {}},
    )
    registry = FakeRegistry(
        tool,
        [ToolResult(status="success", output={}, checkpoint_id=checkpoint_id)],
    )
    engine = DeploymentEngine(registry, store, FakeHealthChecker([False]))  # type: ignore[arg-type]
    result = engine.deploy(make_config())
    assert result.status == "rolled_back"
    assert tool.restores == 1
    assert "candidate health check failed" in result.evidence["failure_reason"]


def test_approval_required_at_cutover_rolls_back_candidate(store: CheckpointStore) -> None:
    tool = FakeTool()
    checkpoint_id = store.snapshot(
        "deploy",
        tool.capture_state({}),
        metadata={"tool": "deploy_windows_service", "payload": {}},
    )
    registry = FakeRegistry(
        tool,
        [ToolResult(status="success", output={}, checkpoint_id=checkpoint_id)],
    )
    cutover = FakeCutover([ApprovalRequired("approval needed", request_id="apr-1")])
    engine = DeploymentEngine(
        registry,  # type: ignore[arg-type]
        store,
        FakeHealthChecker([True]),
        proxy=cutover,  # type: ignore[arg-type]
    )
    with pytest.raises(ApprovalRequired):
        engine.deploy(make_config())
    assert tool.restores == 1
    records = store.list_deployments("example-app")
    assert records[0]["status"] == "rolled_back"
    assert records[0]["details"]["approval_request_id"] == "apr-1"


def test_blue_slot_is_selected_after_green_is_active(store: CheckpointStore) -> None:
    store.record_deployment(
        app="example-app",
        version="1.4.1",
        slot="green",
        status="success",
        checkpoint_id=None,
        details={"active": True},
    )
    registry = FakeRegistry(
        FakeTool(), [ToolResult(status="success", output={}, checkpoint_id=None)]
    )
    engine = DeploymentEngine(registry, store, FakeHealthChecker([True, True]))  # type: ignore[arg-type]
    result = engine.deploy(make_config())
    assert result.previous_slot == "green"
    assert result.active_slot == "blue"
    assert registry.calls[0][1]["arguments"] == ["--port", "8080"]


def test_container_payload_uses_container_ports(store: CheckpointStore) -> None:
    registry = FakeRegistry(
        FakeTool(), [ToolResult(status="success", output={}, checkpoint_id=None)]
    )
    engine = DeploymentEngine(registry, store, FakeHealthChecker([True, True]))  # type: ignore[arg-type]
    config = make_config(
        package_type="windows_container",
        service_account=None,
        executable="example-app.exe",
        container_image="opspilot/example:1.4.2",
        container_port=8080,
    )
    result = engine.deploy(config)
    assert result.status == "success"
    name, payload, _, _ = registry.calls[0]
    assert name == "deploy_windows_container"
    assert payload["host_port"] == 8081
    assert payload["container_port"] == 8080
