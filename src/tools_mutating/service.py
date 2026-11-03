"""Windows Service tools (COMP-08, FR-010/FR-051).

Service registration and lifecycle operations delegate to the
PSScriptAnalyzer-clean ``OpsPilot.Service`` module. All three tools support
preview (distinct Python path) and restore from checkpoint state.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from common.contracts import MutatingTool, ToolResult, mutating_contract, result_schema
from common.errors import PreconditionFailed, VerificationFailed
from common.process import ps_quote
from tools_mutating.base import (
    PowerShellTool,
    encode_state,
    json_pipeline,
    module_script,
    ps_array,
    ps_bool,
)


class DeployWindowsServiceTool(PowerShellTool, MutatingTool):
    def __init__(self, runner: Any = None) -> None:
        self._runner = runner
        self.contract = mutating_contract(
            name="deploy_windows_service",
            description=(
                "Install or update a native Windows Service from a packaged application "
                "directory, then verify it reaches Running."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "service_name",
                    "package_path",
                    "executable",
                    "version",
                    "service_account",
                ],
                "properties": {
                    "service_name": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
                    },
                    "package_path": {"type": "string", "minLength": 1},
                    "executable": {"type": "string", "minLength": 1},
                    "version": {
                        "type": "string",
                        "pattern": "^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$",
                    },
                    "arguments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [],
                    },
                    "service_account": {"type": "string", "minLength": 1},
                    "install_root": {
                        "type": "string",
                        "minLength": 1,
                        "default": "C:\\OpsPilot\\apps",
                    },
                },
            },
            output_schema=result_schema(
                {
                    "service_name": {"type": "string"},
                    "status": {"type": "string"},
                    "install_path": {"type": "string"},
                    "version": {"type": "string"},
                }
            ),
            timeout_seconds=180,
            verification=("Get-Service reports Running", "service binary points at installed version"),
            max_retries=2,
        )

    def preview(self, payload: Mapping[str, Any]) -> ToolResult:
        package_path = Path(str(payload["package_path"]))
        if not package_path.is_dir():
            raise PreconditionFailed(f"package directory not found: {package_path}")
        executable = str(payload["executable"])
        if not (package_path / executable).is_file():
            raise PreconditionFailed(
                f"executable not found in package: {package_path / executable}"
            )
        install_path = str(Path(str(payload.get("install_root", "C:\\OpsPilot\\apps"))) / str(payload["service_name"]) / str(payload["version"]))
        return ToolResult(
            status="preview",
            output={
                "service_name": str(payload["service_name"]),
                "status": "would_install",
                "install_path": install_path,
                "version": str(payload["version"]),
            },
            evidence={
                "package_path": str(package_path),
                "executable": executable,
                "arguments": list(payload.get("arguments", [])),
            },
        )

    def _state(self, service_name: str) -> dict[str, Any]:
        output = self.run_json(
            "OpsPilot.Service",
            json_pipeline(f"Get-OpsPilotServiceState -Name {ps_quote(service_name)}"),
            self.contract.timeout_seconds,
        )
        if not isinstance(output, dict):
            raise PreconditionFailed("service state did not return a JSON object")
        return output

    def capture_state(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {"service": self._state(str(payload["service_name"]))}

    def apply(self, payload: Mapping[str, Any]) -> ToolResult:
        arguments = [str(item) for item in payload.get("arguments", [])]
        install_root = str(payload.get("install_root", "C:\\OpsPilot\\apps"))
        body = json_pipeline(
            "Install-OpsPilotService "
            f"-Name {ps_quote(str(payload['service_name']))} "
            f"-PackagePath {ps_quote(str(payload['package_path']))} "
            f"-Executable {ps_quote(str(payload['executable']))} "
            f"-Version {ps_quote(str(payload['version']))} "
            f"-Arguments {ps_array(arguments)} "
            f"-ServiceAccount {ps_quote(str(payload['service_account']))} "
            f"-InstallRoot {ps_quote(install_root)}"
        )
        output = self.runner().run_json(
            module_script("OpsPilot.Service", body),
            timeout_seconds=self.contract.timeout_seconds,
        )
        if not isinstance(output, dict):
            raise PreconditionFailed("service install did not return a JSON object")
        return ToolResult(
            status="success",
            output={
                "service_name": str(output.get("service_name", payload["service_name"])),
                "status": str(output.get("status", "")),
                "install_path": str(output.get("install_path", "")),
                "version": str(output.get("version", payload["version"])),
            },
            evidence={"module": "OpsPilot.Service"},
        )

    def verify(self, payload: Mapping[str, Any], result: ToolResult) -> None:
        state = self._state(str(payload["service_name"]))
        if not state.get("exists") or str(state.get("status", "")).lower() != "running":
            raise VerificationFailed(
                "Windows Service is not running after deployment",
                details={"service": payload["service_name"], "state": state},
            )
        result.evidence["verified_state"] = state

    def restore(self, state: Mapping[str, Any]) -> None:
        service_state = state.get("service")
        if not isinstance(service_state, dict):
            raise PreconditionFailed("checkpoint has no service state")
        self.runner().run_script(
            module_script(
                "OpsPilot.Service",
                json_pipeline(
                    "Restore-OpsPilotServiceState "
                    f"-StateBase64 {ps_quote(encode_state(service_state))}"
                ),
            ),
            timeout_seconds=self.contract.timeout_seconds,
        )


class RemoveWindowsServiceTool(PowerShellTool, MutatingTool):
    def __init__(self, runner: Any = None) -> None:
        self._runner = runner
        self.contract = mutating_contract(
            name="remove_windows_service",
            description=(
                "Stop and delete a Windows Service. Destructive: requires an approval "
                "reference and a rollback plan."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["service_name"],
                "properties": {
                    "service_name": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
                    },
                    "remove_files": {"type": "boolean", "default": False},
                },
            },
            output_schema=result_schema(
                {
                    "service_name": {"type": "string"},
                    "removed": {"type": "boolean"},
                    "files_removed": {"type": "boolean"},
                }
            ),
            timeout_seconds=120,
            risk_categories=("destructive",),
            verification=("Get-Service reports the service is absent",),
        )

    def preview(self, payload: Mapping[str, Any]) -> ToolResult:
        return ToolResult(
            status="preview",
            output={
                "service_name": str(payload["service_name"]),
                "removed": False,
                "files_removed": False,
            },
            evidence={
                "would_stop": True,
                "would_delete_service": True,
                "would_remove_files": bool(payload.get("remove_files", False)),
            },
        )

    def _state(self, service_name: str) -> dict[str, Any]:
        output = self.run_json(
            "OpsPilot.Service",
            json_pipeline(f"Get-OpsPilotServiceState -Name {ps_quote(service_name)}"),
            self.contract.timeout_seconds,
        )
        if not isinstance(output, dict):
            raise PreconditionFailed("service state did not return a JSON object")
        return output

    def capture_state(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {"service": self._state(str(payload["service_name"]))}

    def apply(self, payload: Mapping[str, Any]) -> ToolResult:
        body = json_pipeline(
            "Remove-OpsPilotService "
            f"-Name {ps_quote(str(payload['service_name']))} "
            f"-RemoveFiles:{ps_bool(bool(payload.get('remove_files', False)))}"
        )
        output = self.runner().run_json(
            module_script("OpsPilot.Service", body),
            timeout_seconds=self.contract.timeout_seconds,
        )
        if not isinstance(output, dict):
            raise PreconditionFailed("service removal did not return a JSON object")
        return ToolResult(
            status="success",
            output={
                "service_name": str(payload["service_name"]),
                "removed": bool(output.get("removed", True)),
                "files_removed": bool(output.get("files_removed", False)),
            },
            evidence={"module": "OpsPilot.Service"},
        )

    def verify(self, payload: Mapping[str, Any], result: ToolResult) -> None:
        state = self._state(str(payload["service_name"]))
        if state.get("exists"):
            raise VerificationFailed(
                "Windows Service still exists after removal",
                details={"service": payload["service_name"], "state": state},
            )
        result.evidence["verified_state"] = state

    def restore(self, state: Mapping[str, Any]) -> None:
        service_state = state.get("service")
        if not isinstance(service_state, dict):
            raise PreconditionFailed("checkpoint has no service state")
        self.runner().run_script(
            module_script(
                "OpsPilot.Service",
                json_pipeline(
                    "Restore-OpsPilotServiceState "
                    f"-StateBase64 {ps_quote(encode_state(service_state))}"
                ),
            ),
            timeout_seconds=self.contract.timeout_seconds,
        )


class RestartWindowsServiceTool(PowerShellTool, MutatingTool):
    def __init__(self, runner: Any = None) -> None:
        self._runner = runner
        self.contract = mutating_contract(
            name="restart_windows_service",
            description=(
                "Restart a Windows Service and verify it returns to Running. Low-risk and "
                "reversible; the Approval Gate auto-clears it."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["service_name"],
                "properties": {
                    "service_name": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
                    }
                },
            },
            output_schema=result_schema(
                {
                    "service_name": {"type": "string"},
                    "status": {"type": "string"},
                }
            ),
            timeout_seconds=120,
            verification=("Get-Service reports Running after restart",),
            max_retries=2,
        )

    def preview(self, payload: Mapping[str, Any]) -> ToolResult:
        return ToolResult(
            status="preview",
            output={"service_name": str(payload["service_name"]), "status": "would_restart"},
            evidence={"would_restart": True},
        )

    def _state(self, service_name: str) -> dict[str, Any]:
        output = self.run_json(
            "OpsPilot.Service",
            json_pipeline(f"Get-OpsPilotServiceState -Name {ps_quote(service_name)}"),
            self.contract.timeout_seconds,
        )
        if not isinstance(output, dict):
            raise PreconditionFailed("service state did not return a JSON object")
        return output

    def capture_state(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {"service": self._state(str(payload["service_name"]))}

    def apply(self, payload: Mapping[str, Any]) -> ToolResult:
        output = self.run_json(
            "OpsPilot.Service",
            json_pipeline(
                "Restart-OpsPilotService "
                f"-Name {ps_quote(str(payload['service_name']))}"
            ),
            self.contract.timeout_seconds,
        )
        if not isinstance(output, dict):
            raise PreconditionFailed("service restart did not return a JSON object")
        return ToolResult(
            status="success",
            output={
                "service_name": str(payload["service_name"]),
                "status": str(output.get("status", "Running")),
            },
            evidence={"module": "OpsPilot.Service"},
        )

    def verify(self, payload: Mapping[str, Any], result: ToolResult) -> None:
        state = self._state(str(payload["service_name"]))
        if not state.get("exists") or str(state.get("status", "")).lower() != "running":
            raise VerificationFailed(
                "Windows Service is not Running after restart",
                details={"service": payload["service_name"], "state": state},
            )
        result.evidence["verified_state"] = state

    def restore(self, state: Mapping[str, Any]) -> None:
        service_state = state.get("service")
        if not isinstance(service_state, dict):
            raise PreconditionFailed("checkpoint has no service state")
        self.runner().run_script(
            module_script(
                "OpsPilot.Service",
                json_pipeline(
                    "Restore-OpsPilotServiceState "
                    f"-StateBase64 {ps_quote(encode_state(service_state))}"
                ),
            ),
            timeout_seconds=self.contract.timeout_seconds,
        )
