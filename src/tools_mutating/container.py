"""Windows Container deployment tool (COMP-08, FR-011).

The PowerShell module refuses to operate unless Docker is in Windows-container
mode and the image itself reports OS=windows. This is the code-level enforcement
of ADR-003 / MASTER_PROMPT.md §1: no silent Linux-container fallback.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from common.contracts import MutatingTool, ToolResult, mutating_contract, result_schema
from common.errors import PreconditionFailed, VerificationFailed
from common.process import ps_quote
from tools_mutating.base import (
    PowerShellTool,
    encode_state,
    json_pipeline,
    module_script,
)


class DeployWindowsContainerTool(PowerShellTool, MutatingTool):
    def __init__(self, runner: Any = None) -> None:
        self._runner = runner
        self.contract = mutating_contract(
            name="deploy_windows_container",
            description=(
                "Run a Windows-container image with a pinned host port and verify the "
                "container stays running. Fails closed if Docker is not in Windows mode."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["container_name", "image", "host_port", "container_port"],
                "properties": {
                    "container_name": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
                    },
                    "image": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}$",
                    },
                    "host_port": {"type": "integer", "minimum": 1, "maximum": 65535},
                    "container_port": {"type": "integer", "minimum": 1, "maximum": 65535},
                },
            },
            output_schema=result_schema(
                {
                    "container_name": {"type": "string"},
                    "image": {"type": "string"},
                    "status": {"type": "string"},
                    "host_port": {"type": "integer"},
                }
            ),
            timeout_seconds=300,
            verification=("docker inspect reports the container running with OS=windows",),
            max_retries=1,
        )

    def preview(self, payload: Mapping[str, Any]) -> ToolResult:
        return ToolResult(
            status="preview",
            output={
                "container_name": str(payload["container_name"]),
                "image": str(payload["image"]),
                "status": "would_start",
                "host_port": int(payload["host_port"]),
            },
            evidence={
                "would_check_docker_ostype": "windows",
                "port_binding": f"{payload['host_port']}:{payload['container_port']}",
            },
        )

    def _state(self, container_name: str) -> dict[str, Any]:
        output = self.run_json(
            "OpsPilot.Container",
            json_pipeline(
                f"Get-OpsPilotContainerState -Name {ps_quote(container_name)}"
            ),
            self.contract.timeout_seconds,
        )
        if not isinstance(output, dict):
            raise PreconditionFailed("container state did not return a JSON object")
        return output

    def capture_state(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {"container": self._state(str(payload["container_name"]))}

    def apply(self, payload: Mapping[str, Any]) -> ToolResult:
        body = json_pipeline(
            "Install-OpsPilotContainer "
            f"-Name {ps_quote(str(payload['container_name']))} "
            f"-Image {ps_quote(str(payload['image']))} "
            f"-HostPort {int(payload['host_port'])} "
            f"-ContainerPort {int(payload['container_port'])}"
        )
        output = self.runner().run_json(
            module_script("OpsPilot.Container", body),
            timeout_seconds=self.contract.timeout_seconds,
        )
        if not isinstance(output, dict):
            raise PreconditionFailed("container deployment did not return a JSON object")
        return ToolResult(
            status="success",
            output={
                "container_name": str(output.get("container_name", payload["container_name"])),
                "image": str(output.get("image", payload["image"])),
                "status": str(output.get("status", "running")),
                "host_port": int(output.get("host_port", payload["host_port"])),
            },
            evidence={"module": "OpsPilot.Container"},
        )

    def verify(self, payload: Mapping[str, Any], result: ToolResult) -> None:
        state = self._state(str(payload["container_name"]))
        if not state.get("exists") or str(state.get("status", "")).lower() != "running":
            raise VerificationFailed(
                "Windows container is not running after deployment",
                details={"container": state},
            )
        if str(state.get("os", "")).lower() != "windows":
            raise VerificationFailed(
                "container image is not a Windows-container image",
                details={"container": state},
            )
        result.evidence["verified_state"] = state

    def restore(self, state: Mapping[str, Any]) -> None:
        container_state = state.get("container")
        if not isinstance(container_state, dict):
            raise PreconditionFailed("checkpoint has no container state")
        self.runner().run_script(
            module_script(
                "OpsPilot.Container",
                json_pipeline(
                    "Restore-OpsPilotContainerState "
                    f"-StateBase64 {ps_quote(encode_state(container_state))}"
                ),
            ),
            timeout_seconds=self.contract.timeout_seconds,
        )
