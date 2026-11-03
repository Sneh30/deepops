"""Tool contracts and shared tool abstractions (SAS.md §4).

All read-only tools implement ``ReadOnlyTool``; all mutating tools implement
``MutatingTool``. The two registries are disjoint by type, which is how the
architecture enforces MASTER_PROMPT.md §3 principle 6 rather than trusting a
convention.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from common.config import validate_document
from common.errors import PreconditionFailed

RiskCategory = Literal[
    "destructive",
    "irreversible",
    "externally_visible",
    "security_sensitive",
]
ToolKind = Literal["read_only", "mutating"]
ToolStatus = Literal["success", "failed", "rolled_back", "preview"]

RISK_CATEGORIES: tuple[RiskCategory, ...] = (
    "destructive",
    "irreversible",
    "externally_visible",
    "security_sensitive",
)

READ_ONLY_ERROR_MODES = (
    "PRECONDITION_FAILED",
    "EXECUTION_FAILED",
    "TIMEOUT_EXCEEDED",
)

MUTATING_ERROR_MODES = (
    "PRECONDITION_FAILED",
    "APPROVAL_REQUIRED",
    "APPROVAL_DENIED",
    "APPROVAL_TIMEOUT",
    "EXECUTION_FAILED",
    "TIMEOUT_EXCEEDED",
    "VERIFICATION_FAILED",
    "CHECKPOINT_FAILED",
)


@dataclass(frozen=True)
class ToolContract:
    name: str
    kind: ToolKind
    description: str
    version: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    timeout_seconds: int
    error_modes: tuple[str, ...]
    risk_categories: tuple[RiskCategory, ...] = ()
    requires_checkpoint: bool = False
    requires_approval_evaluation: bool = False
    max_retries: int | None = None
    retry_base_seconds: float | None = None
    retry_cap_seconds: float | None = None
    retryable_error_modes: tuple[str, ...] = ()
    post_execution_verification: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        policy: dict[str, Any] = {"timeout_seconds": self.timeout_seconds}
        if self.kind == "mutating":
            policy.update(
                {
                    "max_retries": self.max_retries if self.max_retries is not None else 2,
                    "retry_backoff": {
                        "base_seconds": self.retry_base_seconds
                        if self.retry_base_seconds is not None
                        else 5.0,
                        "cap_seconds": self.retry_cap_seconds
                        if self.retry_cap_seconds is not None
                        else 60.0,
                    },
                    "retryable_error_modes": list(self.retryable_error_modes),
                }
            )
        document: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "version": self.version,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "execution_policy": policy,
            "error_modes": list(self.error_modes),
            "risk_categories": list(self.risk_categories),
            "requires_checkpoint": self.requires_checkpoint,
            "requires_approval_evaluation": self.requires_approval_evaluation,
        }
        if self.kind == "mutating":
            document["post_execution_verification"] = list(self.post_execution_verification)
        return document

    def validate(self) -> None:
        validate_document(self.to_dict(), "tool-contract.schema.json")

    def validate_payload(self, payload: Mapping[str, Any]) -> None:
        from jsonschema import Draft202012Validator

        validator = Draft202012Validator(self.input_schema)
        errors = sorted(validator.iter_errors(dict(payload)), key=lambda error: list(error.path))
        if errors:
            messages = []
            for error in errors:
                location = "/".join(str(part) for part in error.path) or "<root>"
                messages.append(f"{location}: {error.message}")
            raise PreconditionFailed(
                f"invalid inputs for tool {self.name}: " + "; ".join(messages),
                details={"errors": messages},
            )


@dataclass
class ToolResult:
    status: ToolStatus
    output: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    checkpoint_id: str | None = None
    attempt_count: int = 0
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "output": self.output,
            "evidence": self.evidence,
            "checkpoint_id": self.checkpoint_id,
            "attempt_count": self.attempt_count,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class ToolContext:
    environment: str = "production"
    actor: str = "agent"
    host: str | None = None
    approval_ref: str | None = None
    request_id: str | None = None

    def with_approval(self, approval_ref: str) -> ToolContext:
        return ToolContext(
            environment=self.environment,
            actor=self.actor,
            host=self.host,
            approval_ref=approval_ref,
            request_id=self.request_id,
        )


class ReadOnlyTool(ABC):
    contract: ToolContract

    @abstractmethod
    def run(self, payload: Mapping[str, Any]) -> ToolResult:
        """Inspect state. Must never mutate anything."""


class MutatingTool(ABC):
    contract: ToolContract

    @abstractmethod
    def preview(self, payload: Mapping[str, Any]) -> ToolResult:
        """Return exactly what would change, without changing anything."""

    @abstractmethod
    def capture_state(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Capture the pre-mutation state stored in COMP-05."""

    @abstractmethod
    def apply(self, payload: Mapping[str, Any]) -> ToolResult:
        """Perform the mutation. Only called with dry_run removed."""

    @abstractmethod
    def verify(self, payload: Mapping[str, Any], result: ToolResult) -> None:
        """Assert the intended post-state; raise VerificationFailed otherwise."""

    @abstractmethod
    def restore(self, state: Mapping[str, Any]) -> None:
        """Restore the checkpointed state after a failed execution."""

    def assess_risk(
        self, payload: Mapping[str, Any], context: ToolContext
    ) -> frozenset[RiskCategory]:
        """Return the risk categories this call falls into.

        The contract's declared categories are the floor: a tool may widen the
        set per call (for example, a production-only deployment is
        externally visible), but it may never silently drop a declared
        category. COMP-04 must see at least what the contract promises.
        """
        return frozenset(self.contract.risk_categories)


def _with_dry_run(input_schema: dict[str, Any]) -> dict[str, Any]:
    schema = dict(input_schema)
    if schema.get("type") != "object":
        raise ValueError("tool input_schema must describe a JSON object")
    properties = dict(schema.get("properties", {}))
    properties.setdefault("dry_run", {"type": "boolean", "default": False})
    schema["properties"] = properties
    return schema


def read_only_contract(
    *,
    name: str,
    description: str,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    timeout_seconds: int = 30,
    version: str = "1.0",
) -> ToolContract:
    contract = ToolContract(
        name=name,
        kind="read_only",
        description=description,
        version=version,
        input_schema=input_schema,
        output_schema=output_schema,
        timeout_seconds=timeout_seconds,
        error_modes=READ_ONLY_ERROR_MODES,
    )
    contract.validate()
    return contract


def mutating_contract(
    *,
    name: str,
    description: str,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    timeout_seconds: int = 120,
    risk_categories: tuple[RiskCategory, ...] = (),
    verification: tuple[str, ...] = ("post-execution state assertion",),
    max_retries: int = 2,
    retry_base_seconds: float = 5.0,
    retry_cap_seconds: float = 60.0,
    version: str = "1.0",
) -> ToolContract:
    contract = ToolContract(
        name=name,
        kind="mutating",
        description=description,
        version=version,
        input_schema=_with_dry_run(input_schema),
        output_schema=output_schema,
        timeout_seconds=timeout_seconds,
        error_modes=MUTATING_ERROR_MODES,
        risk_categories=risk_categories,
        requires_checkpoint=True,
        requires_approval_evaluation=True,
        max_retries=max_retries,
        retry_base_seconds=retry_base_seconds,
        retry_cap_seconds=retry_cap_seconds,
        retryable_error_modes=("EXECUTION_FAILED",),
        post_execution_verification=verification,
    )
    contract.validate()
    return contract


def result_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """Small helper for consistent tool output schemas."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required or list(properties),
        "properties": properties,
    }
