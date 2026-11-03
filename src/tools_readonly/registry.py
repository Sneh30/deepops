"""Read-only tool registry (COMP-02).

This registry accepts only ``ReadOnlyTool`` implementations. Mutating tools
cannot be registered here by construction, which is the architectural split
required by SAS.md §5 / MASTER_PROMPT.md §3 principle 6.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from typing import Any

from audit_log.writer import AuditLog
from common.config import validate_document
from common.contracts import ReadOnlyTool, ToolContext, ToolResult
from common.errors import ExecutionFailed, ToolKindMismatch, ToolNotFound
from common.log import StructuredLogger


class ReadOnlyToolRegistry:
    def __init__(
        self,
        tools: Iterable[ReadOnlyTool] = (),
        *,
        audit_log: AuditLog | None = None,
        logger: StructuredLogger | None = None,
    ) -> None:
        self._tools: dict[str, ReadOnlyTool] = {}
        self._audit = audit_log
        self._logger = logger or StructuredLogger("COMP-02")
        for tool in tools:
            self.register(tool)

    def register(self, tool: ReadOnlyTool) -> None:
        contract = tool.contract
        if contract.kind != "read_only":
            raise ToolKindMismatch(
                f"tool {contract.name} is {contract.kind}; cannot register in read-only registry"
            )
        if contract.name in self._tools:
            raise ValueError(f"duplicate read-only tool: {contract.name}")
        contract.validate()
        self._tools[contract.name] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def describe(self) -> list[dict[str, Any]]:
        return [self._tools[name].contract.to_dict() for name in self.names()]

    def get(self, name: str) -> ReadOnlyTool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFound(f"read-only tool not found: {name}")
        return tool

    def execute(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        context: ToolContext | None = None,
    ) -> ToolResult:
        tool = self.get(name)
        contract = tool.contract
        contract.validate_payload(payload)
        started = time.monotonic()
        try:
            result = tool.run(payload)
            if not isinstance(result, ToolResult):
                raise ExecutionFailed(f"tool {name} returned a non-ToolResult value")
            if result.status not in {"success", "preview"}:
                raise ExecutionFailed(
                    f"read-only tool {name} returned status {result.status}"
                )
            validate_document(result.output, contract.output_schema)
        except Exception as error:
            duration_ms = int((time.monotonic() - started) * 1000)
            self._audit_failure(name, payload, error, duration_ms)
            if isinstance(error, ExecutionFailed):
                raise
            raise ExecutionFailed(
                f"read-only tool {name} failed: {type(error).__name__}",
                details={"error": str(error)},
            ) from error
        result.duration_ms = int((time.monotonic() - started) * 1000)
        if self._audit is not None:
            try:
                self._audit.append(
                    action=name,
                    inputs=dict(payload),
                    outcome="success",
                    checkpoint_id=None,
                    approval_ref=None,
                    evidence={
                        "kind": "read_only",
                        "duration_ms": result.duration_ms,
                        **result.evidence,
                    },
                )
            except OSError as error:
                # Read-only actions have no state to protect; log the audit gap
                # without pretending the diagnostic itself failed.
                self._logger.error(
                    "read-only audit write failed",
                    action=name,
                    error=str(error),
                )
        return result

    def _audit_failure(
        self, name: str, payload: Mapping[str, Any], error: Exception, duration_ms: int
    ) -> None:
        self._logger.error(
            "read-only tool failed",
            action=name,
            error_type=type(error).__name__,
            error=str(error),
        )
        if self._audit is None:
            return
        try:
            self._audit.append(
                action=name,
                inputs=dict(payload),
                outcome="failed",
                checkpoint_id=None,
                approval_ref=None,
                evidence={
                    "kind": "read_only",
                    "duration_ms": duration_ms,
                    "error_mode": getattr(error, "error_mode", "EXECUTION_FAILED"),
                    "error": str(error),
                },
            )
        except OSError as audit_error:
            self._logger.error(
                "read-only failure audit write failed",
                action=name,
                error=str(audit_error),
            )


def build_default_registry(
    *,
    store: Any = None,
    audit_log: AuditLog | None = None,
    logger: StructuredLogger | None = None,
) -> ReadOnlyToolRegistry:
    """Construct the default diagnostic tool set for the control plane."""
    from tools_readonly.health import HttpHealthTool
    from tools_readonly.history import (
        DeploymentHistoryTool,
        IncidentHistoryTool,
        UtilizationTool,
    )
    from tools_readonly.service import ServiceStatusTool
    from tools_readonly.system import DiskUsageTool, ProcessStatusTool, SystemInfoTool

    tools: list[ReadOnlyTool] = [
        ServiceStatusTool(),
        HttpHealthTool(),
        SystemInfoTool(),
        DiskUsageTool(),
        ProcessStatusTool(),
    ]
    if store is not None:
        tools.extend(
            [
                DeploymentHistoryTool(store),
                IncidentHistoryTool(store),
                UtilizationTool(store),
            ]
        )
    return ReadOnlyToolRegistry(tools, audit_log=audit_log, logger=logger)
