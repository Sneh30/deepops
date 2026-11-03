"""Read-only service inspection via PowerShell (COMP-02)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from common.contracts import ReadOnlyTool, ToolResult, read_only_contract, result_schema
from common.errors import ExecutionFailed
from common.process import PowerShellRunner, ps_quote


class ServiceStatusTool(ReadOnlyTool):
    def __init__(self, runner: PowerShellRunner | None = None) -> None:
        self._runner = runner
        self.contract = read_only_contract(
            name="get_service_status",
            description="Inspect a Windows service's existence and current state.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["service_name"],
                "properties": {
                    "service_name": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$",
                    }
                },
            },
            output_schema=result_schema(
                {
                    "exists": {"type": "boolean"},
                    "name": {"type": "string"},
                    "status": {"type": ["string", "null"]},
                }
            ),
            timeout_seconds=30,
        )

    def _runner_instance(self) -> PowerShellRunner:
        if self._runner is None:
            self._runner = PowerShellRunner()
        return self._runner

    def run(self, payload: Mapping[str, Any]) -> ToolResult:
        service_name = str(payload["service_name"])
        script = f"""
$ErrorActionPreference = 'Stop'
$name = {ps_quote(service_name)}
$svc = Get-Service -Name $name -ErrorAction SilentlyContinue
if ($null -eq $svc) {{
    [pscustomobject]@{{ exists = $false; name = $name; status = $null }} | ConvertTo-Json -Compress
}} else {{
    [pscustomobject]@{{ exists = $true; name = $svc.Name; status = $svc.Status.ToString() }} | ConvertTo-Json -Compress
}}
"""
        output = self._runner_instance().run_json(
            script, timeout_seconds=self.contract.timeout_seconds
        )
        if not isinstance(output, dict):
            raise ExecutionFailed("service status tool expected a JSON object")
        return ToolResult(
            status="success",
            output={
                "exists": bool(output.get("exists", False)),
                "name": str(output.get("name", service_name)),
                "status": output.get("status"),
            },
            evidence={"tool": "get_service_status"},
        )
