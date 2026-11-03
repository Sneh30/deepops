"""Secrets manager abstraction (COMP-11, NFR-009).

MVP uses Windows DPAPI through PowerShell, scoped to the service account that
runs the control plane. Secrets are passed to/from PowerShell as base64 over
stdin, never as command-line arguments, and are never written to audit logs.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Mapping
from typing import Any, Protocol

from common.errors import ExecutionFailed, PreconditionFailed
from common.process import PowerShellRunner, ps_quote
from event_bus.publisher import EventPublisher, NullEventPublisher
from tools_mutating.base import json_pipeline, module_script

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class SecretsManager(Protocol):
    def get(self, name: str) -> str | None: ...

    def require(self, name: str) -> str: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> bool: ...

    def list_names(self) -> list[str]: ...


def _validate_name(name: str) -> str:
    if not _NAME_RE.fullmatch(name):
        raise PreconditionFailed(f"invalid secret name: {name!r}")
    return name


class DpapiSecretsManager:
    def __init__(
        self,
        *,
        runner: PowerShellRunner | None = None,
        root_path: str = r"C:\ProgramData\OpsPilot\secrets",
        events: EventPublisher | None = None,
    ) -> None:
        self._runner = runner
        self._root_path = root_path
        self._events = events or NullEventPublisher()

    def _runner_instance(self) -> PowerShellRunner:
        if self._runner is None:
            self._runner = PowerShellRunner()
        return self._runner

    def _call(self, body: str) -> Any:
        return self._runner_instance().run_json(
            module_script("OpsPilot.Secrets", json_pipeline(body)),
            timeout_seconds=30,
        )

    def get(self, name: str) -> str | None:
        name = _validate_name(name)
        try:
            output = self._call(
                f"Get-OpsPilotSecret -Name {ps_quote(name)} -Root {ps_quote(self._root_path)}"
            )
        except ExecutionFailed as error:
            # A corrupt or tampered DPAPI blob makes ConvertTo-SecureString fail.
            # Surface that as a precondition failure rather than a raw PS error.
            raise PreconditionFailed(f"secret could not be decrypted: {name}") from error
        if not isinstance(output, dict):
            raise PreconditionFailed("secret read did not return a JSON object")
        if not output.get("found"):
            return None
        encoded = output.get("secret_base64")
        if not isinstance(encoded, str):
            raise PreconditionFailed("secret read returned no secret payload")
        self._events.publish(
            "secret.accessed",
            component="COMP-11",
            message=f"secret accessed: {name}",
            data={"name": name},
        )
        try:
            return base64.b64decode(encoded.encode("ascii")).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise PreconditionFailed("secret payload was not valid base64 UTF-8") from error

    def require(self, name: str) -> str:
        value = self.get(name)
        if value is None:
            raise PreconditionFailed(f"required secret is not provisioned: {name}")
        return value

    def set(self, name: str, value: str) -> None:
        name = _validate_name(name)
        if not value:
            raise PreconditionFailed("secret value must not be empty")
        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        output = self._call(
            "Set-OpsPilotSecret "
            f"-Name {ps_quote(name)} "
            f"-ValueBase64 {ps_quote(encoded)} "
            f"-Root {ps_quote(self._root_path)} "
            "-Confirm:$false"
        )
        if not isinstance(output, dict) or not output.get("stored"):
            raise PreconditionFailed(f"failed to store secret: {name}")
        self._events.publish(
            "secret.stored",
            component="COMP-11",
            message=f"secret stored: {name}",
            data={"name": name},
        )

    def delete(self, name: str) -> bool:
        name = _validate_name(name)
        output = self._call(
            f"Remove-OpsPilotSecret -Name {ps_quote(name)} -Root {ps_quote(self._root_path)} "
            "-Confirm:$false"
        )
        removed = isinstance(output, dict) and bool(output.get("removed"))
        if removed:
            self._events.publish(
                "secret.deleted",
                component="COMP-11",
                message=f"secret deleted: {name}",
                data={"name": name},
                level="WARN",
            )
        return removed

    def list_names(self) -> list[str]:
        output = self._call(f"Get-OpsPilotSecretList -Root {ps_quote(self._root_path)}")
        if not isinstance(output, Mapping):
            return []
        names = output.get("names", [])
        return sorted(str(name) for name in names)
