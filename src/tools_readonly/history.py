"""Read-only history queries backed by COMP-05 (COMP-02)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from checkpoint_store.store import CheckpointStore
from common.contracts import ReadOnlyTool, ToolResult, read_only_contract, result_schema
from common.errors import PreconditionFailed


class DeploymentHistoryTool(ReadOnlyTool):
    def __init__(self, store: CheckpointStore) -> None:
        self._store = store
        self.contract = read_only_contract(
            name="get_deployment_history",
            description="List recorded deployment attempts, newest first.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "app": {"type": "string", "minLength": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
                },
            },
            output_schema=result_schema(
                {
                    "deployments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "created_at",
                                "app",
                                "version",
                                "slot",
                                "status",
                                "checkpoint_id",
                            ],
                            "properties": {
                                "created_at": {"type": "string"},
                                "app": {"type": "string"},
                                "version": {"type": "string"},
                                "slot": {"type": "string"},
                                "status": {"type": "string"},
                                "checkpoint_id": {"type": ["string", "null"]},
                            },
                        },
                    }
                }
            ),
        )

    def run(self, payload: Mapping[str, Any]) -> ToolResult:
        limit = int(payload.get("limit", 20))
        records = self._store.list_deployments(payload.get("app"))
        trimmed = [
            {
                "created_at": record["created_at"],
                "app": record["app"],
                "version": record["version"],
                "slot": record["slot"],
                "status": record["status"],
                "checkpoint_id": record["checkpoint_id"],
            }
            for record in records[:limit]
        ]
        return ToolResult(
            status="success",
            output={"deployments": trimmed},
            evidence={"tool": "get_deployment_history", "count": len(trimmed)},
        )


class IncidentHistoryTool(ReadOnlyTool):
    def __init__(self, store: CheckpointStore) -> None:
        self._store = store
        self.contract = read_only_contract(
            name="get_recent_incidents",
            description="List incident records within an optional time window.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
                },
            },
            output_schema=result_schema(
                {
                    "incidents": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "id",
                                "app",
                                "service",
                                "status",
                                "detected_at",
                                "resolved_at",
                                "attempts",
                                "summary",
                            ],
                            "properties": {
                                "id": {"type": "string"},
                                "app": {"type": "string"},
                                "service": {"type": "string"},
                                "status": {"type": "string"},
                                "detected_at": {"type": "string"},
                                "resolved_at": {"type": ["string", "null"]},
                                "attempts": {"type": "integer"},
                                "summary": {"type": "string"},
                            },
                        },
                    }
                }
            ),
        )

    def run(self, payload: Mapping[str, Any]) -> ToolResult:
        start = _parse_datetime(payload.get("start"), "start")
        end = _parse_datetime(payload.get("end"), "end")
        records = self._store.list_incidents(start=start, end=end)
        limit = int(payload.get("limit", 50))
        incidents = [
            {
                "id": record.id,
                "app": record.app,
                "service": record.service,
                "status": record.status,
                "detected_at": record.detected_at.isoformat(),
                "resolved_at": record.resolved_at.isoformat() if record.resolved_at else None,
                "attempts": record.attempts,
                "summary": record.summary,
            }
            for record in records[-limit:]
        ]
        return ToolResult(
            status="success",
            output={"incidents": incidents},
            evidence={"tool": "get_recent_incidents", "count": len(incidents)},
        )


class UtilizationTool(ReadOnlyTool):
    def __init__(self, store: CheckpointStore) -> None:
        self._store = store
        self.contract = read_only_contract(
            name="get_utilization",
            description="Return min/avg/max CPU, memory, and disk utilization over a window.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["start", "end"],
                "properties": {
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "host": {"type": "string"},
                    "app": {"type": "string"},
                },
            },
            output_schema=result_schema(
                {
                    "host": {"type": ["string", "null"]},
                    "app": {"type": ["string", "null"]},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "rollup": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["count", "cpu_percent", "memory_mb", "disk_percent"],
                        "properties": {
                            "count": {"type": "integer"},
                            "cpu_percent": {"type": "object"},
                            "memory_mb": {"type": "object"},
                            "disk_percent": {"type": "object"},
                        },
                    },
                }
            ),
        )

    def run(self, payload: Mapping[str, Any]) -> ToolResult:
        start = _parse_datetime(payload["start"], "start")
        end = _parse_datetime(payload["end"], "end")
        if start is None or end is None:
            raise PreconditionFailed("start and end are required")
        if end < start:
            raise PreconditionFailed("end must be after start")
        rollup = self._store.utilization_rollup(
            start=start,
            end=end,
            host=payload.get("host"),
            app=payload.get("app"),
        )
        output = {
            "host": payload.get("host"),
            "app": payload.get("app"),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "rollup": rollup.to_dict(),
        }
        return ToolResult(
            status="success",
            output=output,
            evidence={"tool": "get_utilization", "samples": rollup.count},
        )


def _parse_datetime(value: Any, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PreconditionFailed(f"{name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PreconditionFailed(f"{name} is not valid ISO-8601: {value}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
