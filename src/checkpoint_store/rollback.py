"""One-call checkpoint rollback (IMPL-601, FR-071).

A checkpoint is only useful if restoring it can be proven to have produced the
recorded state. This module re-runs the originating tool's ``capture_state``
after restore and compares canonical JSON; a mismatch is a hard failure, never
a warning.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from checkpoint_store.store import Checkpoint, CheckpointStore
from common.config import canonical_json
from common.contracts import MutatingTool
from common.errors import CheckpointFailed, PreconditionFailed


@dataclass(frozen=True)
class RollbackResult:
    checkpoint_id: str
    tool: str
    restored: bool
    verified: bool
    state_equal: bool | None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "tool": self.tool,
            "restored": self.restored,
            "verified": self.verified,
            "state_equal": self.state_equal,
            "details": self.details,
        }


class ToolResolver:
    def get(self, name: str) -> MutatingTool:  # pragma: no cover - protocol shape
        raise NotImplementedError


class RollbackManager:
    def __init__(self, store: CheckpointStore, tools: Any) -> None:
        self._store = store
        self._tools = tools

    def rollback(self, checkpoint_id: str, *, actor: str = "agent") -> RollbackResult:
        checkpoint = self._store.get(checkpoint_id)
        tool_name, payload = self._describe(checkpoint)
        tool = self._tools.get(tool_name)

        self._store.restore(
            checkpoint_id,
            restorer=lambda state: tool.restore(state),
            actor=actor,
        )

        state_equal: bool | None = None
        if payload is not None:
            try:
                current = tool.capture_state(payload)
            except Exception as error:
                raise CheckpointFailed(
                    f"rollback of {checkpoint_id} could not re-read host state",
                    details={"error": str(error), "tool": tool_name},
                ) from error
            state_equal = canonical_json(current) == canonical_json(checkpoint.state)
            if not state_equal:
                raise CheckpointFailed(
                    f"rollback of {checkpoint_id} did not reproduce the checkpointed state",
                    details={
                        "tool": tool_name,
                        "expected": _json_preview(checkpoint.state),
                        "actual": _json_preview(current),
                    },
                )

        return RollbackResult(
            checkpoint_id=checkpoint_id,
            tool=tool_name,
            restored=True,
            verified=state_equal is not False,
            state_equal=state_equal,
            details={"label": checkpoint.label},
        )

    def _describe(self, checkpoint: Checkpoint) -> tuple[str, dict[str, Any] | None]:
        tool_name = checkpoint.metadata.get("tool")
        if not isinstance(tool_name, str) or not tool_name:
            raise PreconditionFailed(
                f"checkpoint {checkpoint.id} does not identify its originating tool"
            )
        payload = checkpoint.metadata.get("payload")
        if payload is not None and not isinstance(payload, dict):
            raise PreconditionFailed(
                f"checkpoint {checkpoint.id} has an invalid payload record"
            )
        return tool_name, payload


def _json_preview(value: Mapping[str, Any], limit: int = 2000) -> str:
    text = json.dumps(dict(value), sort_keys=True)
    return text if len(text) <= limit else text[:limit] + "..."
