"""Provisioning Engine (COMP-09, FR-001/FR-002).

The declarative host config becomes Terraform variables for infrastructure
(compute/network/NSG) and intent for the Windows-side baseline hardening
module. No secret is ever written into a Terraform variable file.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.config import REPO_ROOT, load_config
from common.errors import PreconditionFailed
from event_bus.publisher import EventPublisher, NullEventPublisher
from provisioning_engine.terraform_runner import (
    TerraformCommand,
    TerraformPlan,
    TerraformRunner,
)


@dataclass(frozen=True)
class ProvisioningPlan:
    variables: dict[str, Any]
    plan: TerraformPlan

    def to_dict(self) -> dict[str, Any]:
        return {"variables": self.variables, "plan": self.plan.summary()}


class ProvisioningEngine:
    def __init__(
        self,
        runner: TerraformRunner,
        *,
        module_dir: str | Path | None = None,
        events: EventPublisher | None = None,
    ) -> None:
        self.runner = runner
        self.module_dir = Path(module_dir or runner.workdir)
        self._events = events or NullEventPublisher()

    @classmethod
    def from_repo(
        cls,
        state_dir: str | Path,
        *,
        module_dir: str | Path | None = None,
        events: EventPublisher | None = None,
    ) -> ProvisioningEngine:
        module = Path(module_dir or REPO_ROOT / "src" / "provisioning_engine" / "terraform")
        runner = TerraformRunner(state_dir)
        return cls(runner, module_dir=module, events=events)

    def variables_from_host_config(
        self, document: Mapping[str, Any], *, overrides: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        host = document.get("host", {})
        network = document.get("network", {})
        if not isinstance(host, Mapping) or not isinstance(network, Mapping):
            raise PreconditionFailed("host config is missing host/network objects")
        variables: dict[str, Any] = {
            "host_name": host["name"],
            "timezone": host.get("timezone", "UTC"),
            "allowed_inbound_tcp_ports": list(network.get("allowed_inbound_tcp_ports", [])),
            "metrics_scrape_cidrs": list(network.get("metrics_scrape_cidrs", [])),
            "rdp_enabled": bool(host.get("rdp_enabled", False)),
            "tags": dict(document.get("tags", {})),
        }
        if overrides:
            variables.update(dict(overrides))
        self._assert_no_secret_keys(variables)
        return variables

    def load_host_config(self, path: str | Path) -> dict[str, Any]:
        return load_config(path, "host-config.schema.json")

    def write_variables(self, variables: Mapping[str, Any], path: str | Path) -> Path:
        self._assert_no_secret_keys(variables)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(dict(variables), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return target

    def plan(
        self,
        host_config: Mapping[str, Any],
        *,
        overrides: Mapping[str, Any] | None = None,
        plan_file: str = "opspilot.tfplan",
    ) -> ProvisioningPlan:
        variables = self.variables_from_host_config(host_config, overrides=overrides)
        var_file = self.write_variables(variables, self.runner.workdir / "opspilot.tfvars.json")
        self._prepare_working_directory()
        self.runner.init()
        plan = self.runner.plan(var_file=var_file, out=plan_file)
        summary = plan.summary()
        change_count = (
            summary["create"] + summary["update"] + summary["delete"] + summary["replace"]
        )
        self._events.publish(
            "provision.planned",
            component="COMP-09",
            message=f"Terraform plan completed: {change_count} change(s)",
            data={"plan": summary, "host": variables.get("host_name")},
        )
        return ProvisioningPlan(variables=variables, plan=plan)

    def apply(
        self,
        host_config: Mapping[str, Any],
        *,
        overrides: Mapping[str, Any] | None = None,
        plan_file: str = "opspilot.tfplan",
    ) -> tuple[ProvisioningPlan, TerraformCommand, dict[str, Any]]:
        provision_plan = self.plan(
            host_config, overrides=overrides, plan_file=plan_file
        )
        if not provision_plan.plan.has_changes:
            self._events.publish(
                "provision.applied",
                component="COMP-09",
                message="Terraform apply skipped: no changes",
                data={"host": provision_plan.variables.get("host_name"), "changed": False},
            )
            return provision_plan, TerraformCommand((), 0, "no changes", ""), self.runner.output()
        command = self.runner.apply(provision_plan.plan.path)
        if command.returncode != 0:
            self._events.publish(
                "provision.failed",
                component="COMP-09",
                message="Terraform apply failed",
                data={
                    "host": provision_plan.variables.get("host_name"),
                    "returncode": command.returncode,
                    "stderr": command.stderr[-2000:],
                },
                level="ERROR",
            )
        else:
            self._events.publish(
                "provision.applied",
                component="COMP-09",
                message="Terraform apply completed",
                data={"host": provision_plan.variables.get("host_name"), "changed": True},
            )
        return provision_plan, command, self.runner.output()

    def _prepare_working_directory(self) -> None:
        """Copy the module into the state directory so plans are reproducible."""
        if self.module_dir.resolve() == self.runner.workdir.resolve():
            return
        for source in sorted(self.module_dir.glob("*.tf")):
            target = self.runner.workdir / source.name
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    def _assert_no_secret_keys(self, variables: Mapping[str, Any]) -> None:
        forbidden = {
            "password",
            "admin_password",
            "client_secret",
            "secret",
            "token",
            "private_key",
        }
        for key in _walk_keys(variables):
            if key.lower() in forbidden:
                raise PreconditionFailed(
                    f"refusing to write secret-shaped Terraform variable: {key}"
                )


def _walk_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            keys.append(str(key))
            keys.extend(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.extend(_walk_keys(child))
    return keys
