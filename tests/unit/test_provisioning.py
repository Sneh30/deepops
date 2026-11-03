from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from common.errors import ExecutionFailed, PreconditionFailed
from provisioning_engine.engine import ProvisioningEngine
from provisioning_engine.terraform_runner import (
    ResourceChange,
    TerraformCommand,
    TerraformPlan,
    TerraformRunner,
)

HOST_CONFIG = {
    "version": "1",
    "host": {
        "name": "opspilot-demo",
        "os_family": "windows",
        "timezone": "UTC",
        "admin_account": "opspilot-admin",
        "service_account": "OPSPILOT\\svc-opspilot",
        "rdp_enabled": False,
    },
    "network": {
        "allowed_inbound_tcp_ports": [80, 443],
        "metrics_scrape_cidrs": ["127.0.0.1/32"],
        "dns_servers": [],
    },
    "features": {"containers": False, "hyper_v": False},
    "tags": {"managed-by": "opspilot-cloud", "environment": "production"},
}


class FakeTerraformExecutor:
    def __init__(self, outputs: dict[str, str]) -> None:
        self.outputs = outputs
        self.calls: list[tuple[str, ...]] = []

    def run(
        self, args: Sequence[str], *, cwd: Path, timeout_seconds: int
    ) -> TerraformCommand:
        command = tuple(args[1:])
        self.calls.append(command)
        stdout = self.outputs.get(command[0], "")
        if command[0] == "show":
            stdout = json.dumps(
                {
                    "resource_changes": [
                        {
                            "address": "azurerm_windows_virtual_machine.host",
                            "change": {"actions": ["create"]},
                        },
                        {
                            "address": "azurerm_network_security_group.host",
                            "change": {"actions": ["no-op"]},
                        },
                    ]
                }
            )
        if command[0] == "plan":
            Path(cwd, "opspilot.tfplan").write_text("plan", encoding="utf-8")
        return TerraformCommand(
            args=tuple(args), returncode=0, stdout=stdout, stderr=""
        )


def test_variables_from_host_config_are_provider_neutral() -> None:
    engine = ProvisioningEngine(TerraformRunner("/tmp/unused"))
    variables = engine.variables_from_host_config(HOST_CONFIG)
    assert variables["host_name"] == "opspilot-demo"
    assert variables["allowed_inbound_tcp_ports"] == [80, 443]
    assert variables["rdp_enabled"] is False
    assert variables["tags"]["managed-by"] == "opspilot-cloud"


def test_variables_reject_secret_shaped_override(tmp_path) -> None:
    engine = ProvisioningEngine(TerraformRunner(tmp_path))
    with pytest.raises(PreconditionFailed):
        engine.variables_from_host_config(
            HOST_CONFIG, overrides={"admin_password": "hunter2"}
        )


def test_write_variables_is_deterministic(tmp_path) -> None:
    engine = ProvisioningEngine(TerraformRunner(tmp_path))
    variables = engine.variables_from_host_config(HOST_CONFIG)
    target = engine.write_variables(variables, tmp_path / "vars.json")
    first = target.read_text(encoding="utf-8")
    engine.write_variables(variables, target)
    assert target.read_text(encoding="utf-8") == first
    assert json.loads(first)["host_name"] == "opspilot-demo"


def test_plan_summarises_changes(tmp_path) -> None:
    executor = FakeTerraformExecutor(
        {"version": json.dumps({"terraform_version": "1.9.8"})}
    )
    runner = TerraformRunner(tmp_path, executor=executor)
    engine = ProvisioningEngine(runner)
    plan = engine.plan(HOST_CONFIG)
    assert plan.plan.create_count == 1
    assert plan.plan.update_count == 0
    assert plan.plan.replace_count == 0
    assert plan.plan.has_changes is True
    assert [call[0] for call in executor.calls] == ["init", "plan", "show"]


def test_apply_stops_when_plan_has_no_changes(tmp_path) -> None:
    class NoChangeExecutor(FakeTerraformExecutor):
        def run(
            self, args: Sequence[str], *, cwd: Path, timeout_seconds: int
        ) -> TerraformCommand:
            if args[1] == "show":
                return TerraformCommand(
                    args=tuple(args),
                    returncode=0,
                    stdout=json.dumps({"resource_changes": []}),
                    stderr="",
                )
            return super().run(args, cwd=cwd, timeout_seconds=timeout_seconds)

    executor = NoChangeExecutor({})
    engine = ProvisioningEngine(TerraformRunner(tmp_path, executor=executor))
    plan, command, _outputs = engine.apply(HOST_CONFIG)
    assert plan.plan.has_changes is False
    assert command.returncode == 0
    assert "apply" not in [call[0] for call in executor.calls]


def test_terraform_failure_is_typed(tmp_path) -> None:
    class FailingExecutor(FakeTerraformExecutor):
        def run(
            self, args: Sequence[str], *, cwd: Path, timeout_seconds: int
        ) -> TerraformCommand:
            return TerraformCommand(tuple(args), 1, "", "boom")

    runner = TerraformRunner(tmp_path, executor=FailingExecutor({}))
    with pytest.raises(ExecutionFailed):
        runner.init()


def test_plan_summary_shape() -> None:
    plan = TerraformPlan(
        path=Path("plan.tfplan"),
        changes=(
            ResourceChange("a", ("create",)),
            ResourceChange("b", ("delete", "create")),
            ResourceChange("c", ("update",)),
        ),
    )
    assert plan.summary()["create"] == 1
    assert plan.summary()["replace"] == 1
    assert plan.summary()["update"] == 1
