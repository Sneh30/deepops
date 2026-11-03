"""Read-only host diagnostics (COMP-02)."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import psutil

from common.contracts import ReadOnlyTool, ToolResult, read_only_contract, result_schema
from common.errors import PreconditionFailed
from common.platform import is_windows


class SystemInfoTool(ReadOnlyTool):
    def __init__(self) -> None:
        self.contract = read_only_contract(
            name="get_system_info",
            description="Report hostname, OS, Python runtime, CPU count, and total memory.",
            input_schema={"type": "object", "additionalProperties": False},
            output_schema=result_schema(
                {
                    "hostname": {"type": "string"},
                    "os": {"type": "string"},
                    "release": {"type": "string"},
                    "platform": {"type": "string"},
                    "python_version": {"type": "string"},
                    "windows": {"type": "boolean"},
                    "cpu_count": {"type": "integer"},
                    "memory_mb": {"type": "number"},
                }
            ),
        )

    def run(self, payload: Mapping[str, Any]) -> ToolResult:
        memory = psutil.virtual_memory()
        output = {
            "hostname": platform.node(),
            "os": platform.system(),
            "release": platform.release(),
            "platform": sys.platform,
            "python_version": platform.python_version(),
            "windows": is_windows(),
            "cpu_count": os.cpu_count() or 1,
            "memory_mb": round(memory.total / (1024 * 1024), 2),
        }
        return ToolResult(status="success", output=output, evidence={"tool": "get_system_info"})


class DiskUsageTool(ReadOnlyTool):
    def __init__(self) -> None:
        self.contract = read_only_contract(
            name="get_disk_usage",
            description="Report filesystem capacity and utilization for a local path.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"path": {"type": "string", "minLength": 1}},
            },
            output_schema=result_schema(
                {
                    "path": {"type": "string"},
                    "total_gb": {"type": "number"},
                    "used_gb": {"type": "number"},
                    "free_gb": {"type": "number"},
                    "percent_used": {"type": "number"},
                }
            ),
        )

    def run(self, payload: Mapping[str, Any]) -> ToolResult:
        default_path = (
            Path(os.environ.get("SYSTEMDRIVE", "C:") + "\\") if is_windows() else Path("/")
        )
        target = Path(str(payload.get("path", default_path)))
        if not target.exists():
            raise PreconditionFailed(f"disk path does not exist: {target}")
        usage = shutil.disk_usage(target)
        total_gb = usage.total / (1024**3)
        used_gb = usage.used / (1024**3)
        output = {
            "path": str(target),
            "total_gb": round(total_gb, 2),
            "used_gb": round(used_gb, 2),
            "free_gb": round(usage.free / (1024**3), 2),
            "percent_used": round((usage.used / usage.total) * 100, 2) if usage.total else 0.0,
        }
        return ToolResult(status="success", output=output, evidence={"tool": "get_disk_usage"})


class ProcessStatusTool(ReadOnlyTool):
    def __init__(self) -> None:
        self.contract = read_only_contract(
            name="get_process_status",
            description="Check whether a named process is running and report its memory use.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["process_name"],
                "properties": {
                    "process_name": {"type": "string", "pattern": "^[A-Za-z0-9._ -]{1,127}$"}
                },
            },
            output_schema=result_schema(
                {
                    "running": {"type": "boolean"},
                    "name": {"type": "string"},
                    "pid": {"type": ["integer", "null"]},
                    "memory_mb": {"type": ["number", "null"]},
                }
            ),
        )

    def _find(self, name: str) -> psutil.Process | None:
        lowered = name.lower()
        for process in psutil.process_iter(["name", "pid"]):
            try:
                process_name = (process.info.get("name") or "").lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if process_name == lowered:
                return process
        return None

    def run(self, payload: Mapping[str, Any]) -> ToolResult:
        name = str(payload["process_name"])
        process = self._find(name)
        if process is None:
            output = {"running": False, "name": name, "pid": None, "memory_mb": None}
        else:
            try:
                memory_mb = round(process.memory_info().rss / (1024 * 1024), 2)
                output = {
                    "running": True,
                    "name": name,
                    "pid": process.pid,
                    "memory_mb": memory_mb,
                }
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                output = {"running": False, "name": name, "pid": None, "memory_mb": None}
        return ToolResult(status="success", output=output, evidence={"tool": "get_process_status"})
