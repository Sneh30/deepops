"""Reverse proxy configuration tool (COMP-10, FR-030/FR-032).

The Caddyfile content is validated and hot-reloaded by the Windows PowerShell
module. The pre-mutation file content and service state are checkpointed so a
failed reload restores the prior proxy configuration.
"""

from __future__ import annotations

import hashlib
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


class ConfigureReverseProxyTool(PowerShellTool, MutatingTool):
    def __init__(self, runner: Any = None) -> None:
        self._runner = runner
        self.contract = mutating_contract(
            name="configure_reverse_proxy",
            description=(
                "Write a Caddyfile, validate it with the Caddy binary, and reload the Caddy "
                "service; restores the previous config on failure."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["config_path", "caddyfile"],
                "properties": {
                    "config_path": {"type": "string", "minLength": 1},
                    "caddyfile": {"type": "string", "minLength": 1},
                    "service_name": {"type": "string", "default": "caddy"},
                },
            },
            output_schema=result_schema(
                {
                    "config_path": {"type": "string"},
                    "validated": {"type": "boolean"},
                    "service_running": {"type": "boolean"},
                }
            ),
            timeout_seconds=120,
            risk_categories=("externally_visible",),
            verification=("caddy validate succeeds", "Caddy service is Running"),
            max_retries=1,
        )

    def preview(self, payload: Mapping[str, Any]) -> ToolResult:
        content = str(payload["caddyfile"])
        if "\x00" in content:
            raise PreconditionFailed("Caddyfile contains a NUL byte")
        if "import " in content:
            raise PreconditionFailed(
                "Caddyfile imports are not permitted; configuration must be self-contained"
            )
        return ToolResult(
            status="preview",
            output={
                "config_path": str(payload["config_path"]),
                "validated": False,
                "service_running": False,
            },
            evidence={
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "bytes": len(content.encode("utf-8")),
                "would_validate_and_reload": True,
            },
        )

    def _state(self, config_path: str, service_name: str) -> dict[str, Any]:
        output = self.run_json(
            "OpsPilot.Proxy",
            json_pipeline(
                "Get-OpsPilotCaddyState "
                f"-ConfigPath {ps_quote(config_path)} "
                f"-ServiceName {ps_quote(service_name)}"
            ),
            self.contract.timeout_seconds,
        )
        if not isinstance(output, dict):
            raise PreconditionFailed("Caddy state did not return a JSON object")
        return output

    def capture_state(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "caddy": self._state(
                str(payload["config_path"]), str(payload.get("service_name", "caddy"))
            )
        }

    def apply(self, payload: Mapping[str, Any]) -> ToolResult:
        body = json_pipeline(
            "Set-OpsPilotCaddyConfig "
            f"-ConfigPath {ps_quote(str(payload['config_path']))} "
            f"-ContentBase64 {ps_quote(encode_state({'content': str(payload['caddyfile'])}))} "
            f"-ServiceName {ps_quote(str(payload.get('service_name', 'caddy')))}"
        )
        output = self.runner().run_json(
            module_script("OpsPilot.Proxy", body),
            timeout_seconds=self.contract.timeout_seconds,
        )
        if not isinstance(output, dict):
            raise PreconditionFailed("Caddy update did not return a JSON object")
        return ToolResult(
            status="success",
            output={
                "config_path": str(payload["config_path"]),
                "validated": bool(output.get("validated", False)),
                "service_running": bool(output.get("service_running", False)),
            },
            evidence={"module": "OpsPilot.Proxy"},
        )

    def verify(self, payload: Mapping[str, Any], result: ToolResult) -> None:
        output = self.run_json(
            "OpsPilot.Proxy",
            json_pipeline(
                "Test-OpsPilotCaddyConfig "
                f"-ConfigPath {ps_quote(str(payload['config_path']))} "
                f"-ServiceName {ps_quote(str(payload.get('service_name', 'caddy')))}"
            ),
            self.contract.timeout_seconds,
        )
        if not isinstance(output, dict) or not output.get("valid") or not output.get(
            "service_running"
        ):
            raise VerificationFailed(
                "Caddy config validation or service state failed after reload",
                details={"result": output},
            )
        result.evidence["verified"] = output

    def restore(self, state: Mapping[str, Any]) -> None:
        caddy_state = state.get("caddy")
        if not isinstance(caddy_state, dict):
            raise PreconditionFailed("checkpoint has no Caddy state")
        self.runner().run_script(
            module_script(
                "OpsPilot.Proxy",
                json_pipeline(
                    "Restore-OpsPilotCaddyState "
                    f"-StateBase64 {ps_quote(encode_state(caddy_state))}"
                ),
            ),
            timeout_seconds=self.contract.timeout_seconds,
        )
