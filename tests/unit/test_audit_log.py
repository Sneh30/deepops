from __future__ import annotations

import json
from pathlib import Path

import pytest

from audit_log.writer import AuditLog


def test_audit_entry_has_required_fields(audit: AuditLog) -> None:
    entry = audit.append(
        action="restart_windows_service",
        inputs={"service_name": "demo"},
        outcome="success",
        checkpoint_id="ckpt-1",
        approval_ref=None,
        evidence={"attempts": 1},
    )
    payload = entry.to_dict()
    assert set(payload) == {
        "timestamp",
        "action",
        "inputs",
        "outcome",
        "checkpoint_id",
        "approval_ref",
        "evidence",
    }
    assert audit.read_all()[0].action == "restart_windows_service"


def test_audit_redacts_secrets(audit: AuditLog) -> None:
    audit.append(
        action="test",
        inputs={
            "password": "hunter2",
            "api_key": "abc123",
            "credential_ref": "smtp-primary",
            "nested": {"token": "shhh", "safe": "visible"},
        },
        outcome="success",
        checkpoint_id=None,
        approval_ref=None,
        evidence={"error": "Authorization: Bearer abcdefghijklmnop"},
    )
    raw = audit.path.read_text(encoding="utf-8")
    assert "hunter2" not in raw
    assert "abc123" not in raw
    assert "shhh" not in raw
    assert "abcdefghijklmnop" not in raw
    assert "smtp-primary" in raw
    assert "visible" in raw


def test_audit_rejects_invalid_outcome(audit: AuditLog) -> None:
    with pytest.raises(ValueError):
        audit.append(
            action="test",
            inputs={},
            outcome="maybe",
            checkpoint_id=None,
            approval_ref=None,
            evidence={},
        )


def test_audit_rotation(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl", max_bytes=200, max_files=3)
    for index in range(20):
        log.append(
            action=f"action-{index}",
            inputs={"index": index},
            outcome="success",
            checkpoint_id=None,
            approval_ref=None,
            evidence={},
        )
    assert (tmp_path / "audit.jsonl").exists()
    assert any((tmp_path / f"audit.jsonl.{i}").exists() for i in range(1, 3))
    entries = log.read_all()
    assert entries
    assert entries[-1].action == "action-19"
    # Rotated files remain valid JSON Lines.
    for path in tmp_path.glob("audit.jsonl*"):
        for line in path.read_text(encoding="utf-8").splitlines():
            json.loads(line)


def test_audit_query_filters(audit: AuditLog) -> None:
    audit.append(
        action="a",
        inputs={},
        outcome="success",
        checkpoint_id=None,
        approval_ref=None,
        evidence={},
        timestamp="2026-01-01T00:00:00Z",
    )
    audit.append(
        action="b",
        inputs={},
        outcome="failed",
        checkpoint_id=None,
        approval_ref=None,
        evidence={},
        timestamp="2026-01-02T00:00:00Z",
    )
    assert [entry.action for entry in audit.query(outcome="failed")] == ["b"]
    assert [entry.action for entry in audit.query(action="a")] == ["a"]
