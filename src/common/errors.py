"""Typed error hierarchy shared by all components.

Every error exposes an ``error_mode`` string matching the enum in
``config/schema/tool-contract.schema.json`` so tool results and audit records
stay machine-parseable (ENGINEERING_REFERENCE.md §7).
"""

from __future__ import annotations

from typing import Any


class OpsPilotError(Exception):
    """Base class for expected operational failures."""

    error_mode = "EXECUTION_FAILED"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_mode": self.error_mode,
            "message": self.message,
            "details": self.details,
        }


class ConfigValidationError(OpsPilotError):
    """A config document did not satisfy its JSON Schema."""

    error_mode = "PRECONDITION_FAILED"


class PlatformViolation(OpsPilotError):
    """A Windows-only runtime operation was attempted off-Windows."""

    error_mode = "PRECONDITION_FAILED"


class PreconditionFailed(OpsPilotError):
    """A documented tool precondition was not met."""

    error_mode = "PRECONDITION_FAILED"


class ApprovalRequired(OpsPilotError):
    """A gated action was attempted without an approval reference."""

    error_mode = "APPROVAL_REQUIRED"

    def __init__(self, message: str, *, request_id: str) -> None:
        super().__init__(message, details={"request_id": request_id})
        self.request_id = request_id


class ApprovalDenied(OpsPilotError):
    """An approval reference was invalid, rejected, or already used."""

    error_mode = "APPROVAL_DENIED"


class ApprovalTimeout(OpsPilotError):
    """An approval request expired before a decision."""

    error_mode = "APPROVAL_TIMEOUT"


class ExecutionFailed(OpsPilotError):
    """A tool's execution step returned a failure."""

    error_mode = "EXECUTION_FAILED"


class TimeoutExceeded(OpsPilotError):
    """A tool exceeded its hard execution timeout."""

    error_mode = "TIMEOUT_EXCEEDED"


class VerificationFailed(OpsPilotError):
    """Post-execution verification did not confirm the intended state."""

    error_mode = "VERIFICATION_FAILED"


class CheckpointFailed(OpsPilotError):
    """A checkpoint could not be created, read, or verified."""

    error_mode = "CHECKPOINT_FAILED"


class CheckpointIntegrityError(CheckpointFailed):
    """A checkpoint's stored checksum did not match its payload."""


class ToolNotFound(OpsPilotError):
    """No tool with the requested name is registered."""

    error_mode = "PRECONDITION_FAILED"


class ToolKindMismatch(OpsPilotError):
    """A tool was invoked through the wrong (read-only/mutating) registry."""

    error_mode = "PRECONDITION_FAILED"
