"""Stable identifiers for checkpoints, approvals, and incidents."""

from __future__ import annotations

import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def checkpoint_id() -> str:
    return new_id("ckpt")


def approval_id() -> str:
    return new_id("apr")


def incident_id() -> str:
    return new_id("inc")


def request_id() -> str:
    return new_id("req")
