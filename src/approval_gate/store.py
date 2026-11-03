"""Persistent approval request store (SQLite, same database as COMP-05).

An approval reference authorizes exactly one execution. The atomic ``consume``
method prevents replay and is the code-level guarantee behind NFR-008.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from checkpoint_store.schema import SCHEMA_SQL, connect_sql
from common.clock import Clock, SystemClock
from common.errors import ApprovalDenied, ApprovalTimeout, PreconditionFailed
from common.ids import approval_id as new_approval_id

STATUSES = {"pending", "approved", "rejected", "expired", "consumed"}


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    tool_name: str
    payload_digest: str
    risk_categories: frozenset[str]
    status: str
    requested_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    decided_by: str | None
    note: str
    rollback_plan: str
    used_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool_name": self.tool_name,
            "payload_digest": self.payload_digest,
            "risk_categories": sorted(self.risk_categories),
            "status": self.status,
            "requested_at": _iso(self.requested_at),
            "expires_at": _iso(self.expires_at),
            "decided_at": _iso(self.decided_at) if self.decided_at else None,
            "decided_by": self.decided_by,
            "note": self.note,
            "rollback_plan": self.rollback_plan,
            "used_at": _iso(self.used_at) if self.used_at else None,
        }


class ApprovalStore(Protocol):
    def create(
        self,
        *,
        tool_name: str,
        payload_digest: str,
        risk_categories: frozenset[str],
        ttl_seconds: int,
        rollback_plan: str = "",
    ) -> ApprovalRequest: ...

    def get(self, request_id: str) -> ApprovalRequest: ...

    def list_pending(self) -> list[ApprovalRequest]: ...

    def decide(
        self,
        request_id: str,
        *,
        approved: bool,
        approver: str,
        note: str = "",
        rollback_plan: str | None = None,
    ) -> ApprovalRequest: ...

    def consume(
        self, request_id: str, *, tool_name: str, payload_digest: str
    ) -> ApprovalRequest: ...


class SqliteApprovalStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Clock | None = None,
        id_factory: Callable[[], str] = new_approval_id,
    ) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or SystemClock()
        self._id_factory = id_factory
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = connect_sql(self.db_path)
        connection.executescript(SCHEMA_SQL)
        row = connection.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
        if row is None:
            connection.execute("INSERT INTO schema_meta(version) VALUES (1)")
        connection.commit()
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        connection.close()

    def create(
        self,
        *,
        tool_name: str,
        payload_digest: str,
        risk_categories: frozenset[str],
        ttl_seconds: int,
        rollback_plan: str = "",
    ) -> ApprovalRequest:
        if ttl_seconds < 60:
            raise PreconditionFailed("approval TTL must be at least 60 seconds")
        identifier = self._id_factory()
        requested_at = self._clock.now()
        expires_at = requested_at + timedelta(seconds=ttl_seconds)
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO approval_requests
                    (id, tool_name, payload_digest, risk_categories_json, status,
                     requested_at, expires_at, decided_at, decided_by, note,
                     rollback_plan, used_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?, NULL, NULL, '', ?, NULL)
                """,
                (
                    identifier,
                    tool_name,
                    payload_digest,
                    json.dumps(sorted(risk_categories)),
                    _iso(requested_at),
                    _iso(expires_at),
                    rollback_plan,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get(identifier)

    def get(self, request_id: str) -> ApprovalRequest:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE id = ?", (request_id,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise ApprovalDenied(f"approval request not found: {request_id}")
        return _request_from_row(row)

    def list_pending(self) -> list[ApprovalRequest]:
        self.expire_overdue()
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM approval_requests WHERE status = 'pending' "
                "ORDER BY requested_at ASC"
            ).fetchall()
        finally:
            connection.close()
        return [_request_from_row(row) for row in rows]

    def expire_overdue(self) -> int:
        now = _iso(self._clock.now())
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE approval_requests SET status = 'expired' "
                "WHERE status = 'pending' AND expires_at < ?",
                (now,),
            )
            connection.commit()
            return int(cursor.rowcount)
        finally:
            connection.close()

    def decide(
        self,
        request_id: str,
        *,
        approved: bool,
        approver: str,
        note: str = "",
        rollback_plan: str | None = None,
    ) -> ApprovalRequest:
        if not approver.strip():
            raise PreconditionFailed("approver identity is required")
        request = self.get(request_id)
        if request.status == "expired":
            raise ApprovalTimeout(f"approval request {request_id} expired")
        if request.status != "pending":
            raise PreconditionFailed(
                f"approval request {request_id} is {request.status}, not pending"
            )
        now = self._clock.now()
        if request.expires_at < now:
            self.expire_overdue()
            raise ApprovalTimeout(f"approval request {request_id} expired")
        status = "approved" if approved else "rejected"
        plan = request.rollback_plan if rollback_plan is None else rollback_plan
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                UPDATE approval_requests
                SET status = ?, decided_at = ?, decided_by = ?, note = ?, rollback_plan = ?
                WHERE id = ? AND status = 'pending'
                """,
                (status, _iso(now), approver, note, plan, request_id),
            )
            connection.commit()
            if cursor.rowcount != 1:
                raise PreconditionFailed(
                    f"approval request {request_id} changed state during decision"
                )
        finally:
            connection.close()
        return self.get(request_id)

    def consume(
        self, request_id: str, *, tool_name: str, payload_digest: str
    ) -> ApprovalRequest:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise ApprovalDenied(f"approval reference not found: {request_id}")
            request = _request_from_row(row)
            if request.status != "approved":
                raise ApprovalDenied(
                    f"approval reference {request_id} is {request.status}, not approved"
                )
            if request.used_at is not None:
                raise ApprovalDenied(f"approval reference {request_id} was already used")
            if request.expires_at < self._clock.now():
                raise ApprovalTimeout(f"approval reference {request_id} expired")
            if request.tool_name != tool_name:
                raise ApprovalDenied(
                    "approval reference does not authorize this tool",
                    details={"expected_tool": request.tool_name, "actual_tool": tool_name},
                )
            if request.payload_digest != payload_digest:
                raise ApprovalDenied(
                    "approval reference does not match the exact payload",
                    details={"expected_digest": request.payload_digest},
                )
            cursor = connection.execute(
                "UPDATE approval_requests SET status = 'consumed', used_at = ? "
                "WHERE id = ? AND status = 'approved' AND used_at IS NULL",
                (_iso(self._clock.now()), request_id),
            )
            connection.commit()
            if cursor.rowcount != 1:
                raise ApprovalDenied(
                    f"approval reference {request_id} was consumed by another caller"
                )
        finally:
            connection.close()
        return self.get(request_id)


def _request_from_row(row: sqlite3.Row) -> ApprovalRequest:
    status = row["status"]
    if status not in STATUSES:
        raise ApprovalDenied(f"corrupt approval status in store: {status}")
    return ApprovalRequest(
        id=row["id"],
        tool_name=row["tool_name"],
        payload_digest=row["payload_digest"],
        risk_categories=frozenset(json.loads(row["risk_categories_json"])),
        status=status,
        requested_at=_parse(row["requested_at"]) or datetime.now(UTC),
        expires_at=_parse(row["expires_at"]) or datetime.now(UTC),
        decided_at=_parse(row["decided_at"]),
        decided_by=row["decided_by"],
        note=row["note"],
        rollback_plan=row["rollback_plan"],
        used_at=_parse(row["used_at"]),
    )
