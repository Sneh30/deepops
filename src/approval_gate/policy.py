"""Approval policy loading and machine-readable risk evaluation (FR-092)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.config import load_config, validate_document
from common.contracts import RISK_CATEGORIES, RiskCategory


@dataclass(frozen=True)
class ApprovalPolicy:
    required_categories: frozenset[str]
    request_ttl_seconds: int
    require_rollback_plan: bool

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> ApprovalPolicy:
        validate_document(document, "approval-policy.schema.json")
        return cls(
            required_categories=frozenset(document["approval_required_categories"]),
            request_ttl_seconds=int(document["request_ttl_seconds"]),
            require_rollback_plan=bool(document["require_rollback_plan"]),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> ApprovalPolicy:
        return cls.from_document(
            load_config(path, "approval-policy.schema.json")
        )

    def requires_approval(self, categories: Iterable[str]) -> bool:
        requested = set(categories)
        unknown = requested.difference(RISK_CATEGORIES)
        if unknown:
            raise ValueError(f"unknown risk categories: {sorted(unknown)}")
        return bool(requested.intersection(self.required_categories))


def normalize_risk_categories(categories: Iterable[str]) -> frozenset[RiskCategory]:
    requested = frozenset(categories)
    unknown = requested.difference(RISK_CATEGORIES)
    if unknown:
        raise ValueError(f"unknown risk categories: {sorted(unknown)}")
    return frozenset(category for category in requested if category in RISK_CATEGORIES)
