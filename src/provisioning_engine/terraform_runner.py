"""Terraform execution wrapper (COMP-09, FR-002).

Terraform is invoked as the official Windows binary; this module never assumes
a bash shell, WSL, or a POSIX-only Terraform distribution. The wrapper captures
structured plan/apply output so callers can make decisions from evidence rather
than scraping human-readable text.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from common.errors import ExecutionFailed, PreconditionFailed


@dataclass(frozen=True)
class TerraformCommand:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class TerraformExecutor(Protocol):
    def run(
        self, args: Sequence[str], *, cwd: Path, timeout_seconds: int
    ) -> TerraformCommand: ...


class SubprocessTerraformExecutor:
    def run(
        self, args: Sequence[str], *, cwd: Path, timeout_seconds: int
    ) -> TerraformCommand:
        try:
            completed = subprocess.run(
                list(args),
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                shell=False,
            )
        except FileNotFoundError as error:
            raise PreconditionFailed(
                "terraform binary not found; install the pinned Windows Terraform build "
                "and put it on PATH"
            ) from error
        except subprocess.TimeoutExpired as error:
            raise ExecutionFailed(
                f"terraform command exceeded {timeout_seconds}s",
                details={"args": list(args)},
            ) from error
        return TerraformCommand(
            args=tuple(args),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


@dataclass(frozen=True)
class ResourceChange:
    address: str
    actions: tuple[str, ...]

    @property
    def creates(self) -> bool:
        return "create" in self.actions

    @property
    def updates(self) -> bool:
        return "update" in self.actions

    @property
    def deletes(self) -> bool:
        return "delete" in self.actions

    @property
    def replaces(self) -> bool:
        return "create" in self.actions and "delete" in self.actions

    @property
    def is_noop(self) -> bool:
        return set(self.actions) <= {"no-op", "read"}


@dataclass(frozen=True)
class TerraformPlan:
    path: Path
    changes: tuple[ResourceChange, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def has_changes(self) -> bool:
        return any(not change.is_noop for change in self.changes)

    @property
    def create_count(self) -> int:
        return sum(1 for change in self.changes if change.creates and not change.replaces)

    @property
    def update_count(self) -> int:
        return sum(1 for change in self.changes if change.updates and not change.replaces)

    @property
    def delete_count(self) -> int:
        return sum(1 for change in self.changes if change.deletes and not change.replaces)

    @property
    def replace_count(self) -> int:
        return sum(1 for change in self.changes if change.replaces)

    def summary(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "has_changes": self.has_changes,
            "create": self.create_count,
            "update": self.update_count,
            "delete": self.delete_count,
            "replace": self.replace_count,
        }

    @classmethod
    def from_json(cls, document: Mapping[str, Any], path: Path) -> TerraformPlan:
        changes: list[ResourceChange] = []
        for item in document.get("resource_changes", []) or []:
            if not isinstance(item, Mapping):
                continue
            change = item.get("change", {})
            actions = change.get("actions", []) if isinstance(change, Mapping) else []
            changes.append(
                ResourceChange(
                    address=str(item.get("address", "")),
                    actions=tuple(str(action) for action in actions),
                )
            )
        return cls(path=path, changes=tuple(changes), raw=dict(document))


class TerraformRunner:
    def __init__(
        self,
        workdir: str | Path,
        *,
        binary: str = "terraform",
        executor: TerraformExecutor | None = None,
        timeout_seconds: int = 900,
    ) -> None:
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.binary = binary
        self.executor = executor or SubprocessTerraformExecutor()
        self.timeout_seconds = timeout_seconds

    def _run(self, args: Sequence[str], timeout_seconds: int | None = None) -> TerraformCommand:
        command = (self.binary, *args)
        result = self.executor.run(
            command,
            cwd=self.workdir,
            timeout_seconds=timeout_seconds or self.timeout_seconds,
        )
        return result

    def _require(self, result: TerraformCommand, action: str) -> TerraformCommand:
        if not result.ok:
            raise ExecutionFailed(
                f"terraform {action} failed with exit code {result.returncode}",
                details={
                    "stdout": result.stdout[-4000:],
                    "stderr": result.stderr[-4000:],
                },
            )
        return result

    def version(self) -> str:
        result = self._require(self._run(["version", "-json"]), "version")
        try:
            document = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ExecutionFailed("terraform version did not return JSON") from error
        return str(document.get("terraform_version", "unknown"))

    def init(self, *, upgrade: bool = False) -> TerraformCommand:
        args = ["init", "-input=false", "-no-color"]
        if upgrade:
            args.append("-upgrade")
        return self._require(self._run(args), "init")

    def plan(
        self,
        *,
        var_file: str | Path | None = None,
        out: str = "opspilot.tfplan",
        destroy: bool = False,
    ) -> TerraformPlan:
        args = ["plan", "-input=false", "-no-color", f"-out={out}"]
        if var_file is not None:
            args.append(f"-var-file={var_file}")
        if destroy:
            args.append("-destroy")
        self._require(self._run(args), "plan")
        plan_path = self.workdir / out
        if not plan_path.exists():
            raise ExecutionFailed(f"terraform plan did not produce {plan_path}")
        return self.show(plan_path)

    def show(self, plan_path: str | Path) -> TerraformPlan:
        path = Path(plan_path)
        if not path.is_absolute():
            path = self.workdir / path
        result = self._require(self._run(["show", "-json", str(path)]), "show")
        try:
            document = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ExecutionFailed("terraform show returned invalid JSON") from error
        return TerraformPlan.from_json(document, path)

    def apply(self, plan_path: str | Path) -> TerraformCommand:
        path = Path(plan_path)
        if not path.is_absolute():
            path = self.workdir / path
        return self._require(
            self._run(["apply", "-input=false", "-no-color", str(path)]), "apply"
        )

    def destroy(self, *, var_file: str | Path | None = None) -> TerraformCommand:
        args = ["destroy", "-input=false", "-no-color", "-auto-approve"]
        if var_file is not None:
            args.append(f"-var-file={var_file}")
        return self._require(self._run(args), "destroy")

    def output(self) -> dict[str, Any]:
        result = self._require(self._run(["output", "-json", "-no-color"]), "output")
        if not result.stdout.strip():
            return {}
        try:
            document = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ExecutionFailed("terraform output returned invalid JSON") from error
        return {
            key: value.get("value") if isinstance(value, Mapping) else value
            for key, value in document.items()
        }
