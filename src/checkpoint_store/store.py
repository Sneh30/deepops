"""Checkpoint / State Store (COMP-05, FR-070/FR-071, ADR-006).

A checkpoint is a canonical JSON state blob written before a mutation. Reads
verify the SHA-256 checksum; a mismatch is a hard stop (SAS.md §10) — never
restore an unverifiable checkpoint.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from checkpoint_store.schema import SCHEMA_SQL, SCHEMA_VERSION, connect_sql
from common.clock import Clock, SystemClock
from common.config import canonical_json, sha256_hex
from common.errors import CheckpointFailed, CheckpointIntegrityError
from common.ids import checkpoint_id as new_checkpoint_id
from common.redaction import redact
from event_bus.publisher import EventPublisher, NullEventPublisher


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class Checkpoint:
    id: str
    created_at: datetime
    label: str
    state: dict[str, Any]
    checksum: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class IncidentRecord:
    id: str
    app: str
    service: str
    status: str
    detected_at: datetime
    resolved_at: datetime | None
    attempts: int
    summary: str
    details: dict[str, Any]


@dataclass(frozen=True)
class UtilizationRollup:
    count: int
    cpu_min: float
    cpu_avg: float
    cpu_max: float
    memory_mb_min: float
    memory_mb_avg: float
    memory_mb_max: float
    disk_percent_min: float
    disk_percent_avg: float
    disk_percent_max: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "cpu_percent": {
                "min": round(self.cpu_min, 2),
                "avg": round(self.cpu_avg, 2),
                "max": round(self.cpu_max, 2),
            },
            "memory_mb": {
                "min": round(self.memory_mb_min, 2),
                "avg": round(self.memory_mb_avg, 2),
                "max": round(self.memory_mb_max, 2),
            },
            "disk_percent": {
                "min": round(self.disk_percent_min, 2),
                "avg": round(self.disk_percent_avg, 2),
                "max": round(self.disk_percent_max, 2),
            },
        }


class CheckpointStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Clock | None = None,
        id_factory: Callable[[], str] = new_checkpoint_id,
        events: EventPublisher | None = None,
    ) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or SystemClock()
        self._id_factory = id_factory
        self._events = events or NullEventPublisher()
        self._memory: sqlite3.Connection | None = None
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self.db_path == ":memory:":
            if self._memory is None:
                self._memory = connect_sql(":memory:")
            yield self._memory
            self._memory.commit()
            return
        connection = connect_sql(self.db_path)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(SCHEMA_SQL)
            row = connection.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,)
                )
            elif int(row["version"]) != SCHEMA_VERSION:
                raise CheckpointFailed(
                    f"state store schema version {row['version']} does not match "
                    f"expected {SCHEMA_VERSION}"
                )

    # ------------------------------------------------------------------
    # Checkpoints
    # ------------------------------------------------------------------
    def snapshot(
        self,
        label: str,
        state: Mapping[str, Any],
        *,
        metadata: Mapping[str, Any] | None = None,
        checkpoint_id: str | None = None,
    ) -> str:
        if not label:
            raise CheckpointFailed("checkpoint label is required")
        try:
            state_json = canonical_json(dict(state))
            json.loads(state_json)
        except (TypeError, ValueError) as error:
            raise CheckpointFailed(
                "state is not JSON-serializable", details={"error": str(error)}
            ) from error
        identifier = checkpoint_id or self._id_factory()
        metadata_json = canonical_json(dict(metadata or {}))
        checksum = sha256_hex(state_json)
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO checkpoints
                        (id, created_at, label, state_json, checksum, metadata_json, schema_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        _iso(self._clock.now()),
                        label,
                        state_json,
                        checksum,
                        metadata_json,
                        SCHEMA_VERSION,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise CheckpointFailed(
                f"checkpoint id already exists: {identifier}", details={"error": str(error)}
            ) from error
        except sqlite3.Error as error:
            raise CheckpointFailed(
                "failed to write checkpoint", details={"error": str(error)}
            ) from error
        # Confirm the read path immediately; a checkpoint that cannot be read
        # back is not a valid checkpoint.
        self.get(identifier)
        self._events.publish(
            "checkpoint.created",
            component="COMP-05",
            message=f"checkpoint created: {label}",
            data={"checkpoint_id": identifier, "label": label},
        )
        return identifier

    def get(self, checkpoint_id: str) -> Checkpoint:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT * FROM checkpoints WHERE id = ?", (checkpoint_id,)
                ).fetchone()
        except sqlite3.Error as error:
            raise CheckpointFailed(
                "failed to read checkpoint", details={"error": str(error)}
            ) from error
        if row is None:
            raise CheckpointFailed(f"checkpoint not found: {checkpoint_id}")
        actual = sha256_hex(row["state_json"])
        if actual != row["checksum"]:
            raise CheckpointIntegrityError(
                f"checkpoint {checkpoint_id} failed checksum verification",
                details={"expected": row["checksum"], "actual": actual},
            )
        try:
            state = json.loads(row["state_json"])
            metadata = json.loads(row["metadata_json"])
        except json.JSONDecodeError as error:
            raise CheckpointIntegrityError(
                f"checkpoint {checkpoint_id} is not valid JSON",
                details={"error": str(error)},
            ) from error
        return Checkpoint(
            id=row["id"],
            created_at=_parse_iso(row["created_at"]),
            label=row["label"],
            state=state,
            checksum=row["checksum"],
            metadata=metadata,
        )

    def load_state(self, checkpoint_id: str) -> dict[str, Any]:
        return self.get(checkpoint_id).state

    def restore(
        self,
        checkpoint_id: str,
        *,
        restorer: Callable[[dict[str, Any]], None] | None = None,
        actor: str = "agent",
    ) -> dict[str, Any]:
        """Verify then restore. The restorer receives the verified state blob."""
        checkpoint = self.get(checkpoint_id)
        detail = "state loaded; no external restorer supplied"
        success = True
        try:
            if restorer is not None:
                restorer(json.loads(canonical_json(checkpoint.state)))
                detail = "external restorer completed"
        except Exception as error:
            success = False
            detail = f"restorer failed: {type(error).__name__}"
            self._record_restore(checkpoint_id, actor, False, detail)
            raise CheckpointFailed(
                f"restore of checkpoint {checkpoint_id} failed",
                details={"error": str(error)},
            ) from error
        self._record_restore(checkpoint_id, actor, success, detail)
        return checkpoint.state

    def _record_restore(
        self, checkpoint_id: str, actor: str, success: bool, detail: str
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO restore_events(checkpoint_id, restored_at, actor, success, detail)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    checkpoint_id,
                    _iso(self._clock.now()),
                    actor,
                    1 if success else 0,
                    detail,
                ),
            )
        self._events.publish(
            "checkpoint.restored" if success else "checkpoint.restore_failed",
            component="COMP-05",
            message=(
                f"checkpoint restored: {checkpoint_id}"
                if success
                else f"checkpoint restore failed: {checkpoint_id}"
            ),
            data={
                "checkpoint_id": checkpoint_id,
                "actor": actor,
                "success": success,
                "detail": detail,
            },
            level="INFO" if success else "ERROR",
        )

    def list_checkpoints(self, limit: int = 50) -> list[Checkpoint]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM checkpoints ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self.get(row["id"]) for row in rows]

    def export_backup(self, destination: str | Path) -> Path:
        """Consistent SQLite backup for disaster recovery (IMPLEMENTATION.md §10)."""
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.db_path == ":memory:":
            raise CheckpointFailed("cannot back up an in-memory store")
        with self._connection() as source:
            backup = sqlite3.connect(str(target))
            try:
                source.backup(backup)
            finally:
                backup.close()
        return target

    # ------------------------------------------------------------------
    # Incidents
    # ------------------------------------------------------------------
    def create_incident(
        self,
        *,
        app: str,
        service: str,
        summary: str,
        details: Mapping[str, Any] | None = None,
        incident_id: str,
        status: str = "Detected",
    ) -> IncidentRecord:
        details_json = canonical_json(redact(dict(details or {})))
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO incidents
                    (id, app, service, status, detected_at, resolved_at, attempts, summary, details_json)
                VALUES (?, ?, ?, ?, ?, NULL, 0, ?, ?)
                """,
                (
                    incident_id,
                    app,
                    service,
                    status,
                    _iso(self._clock.now()),
                    summary,
                    details_json,
                ),
            )
        return self.get_incident(incident_id)

    def update_incident(
        self,
        incident_id: str,
        *,
        status: str,
        attempts: int | None = None,
        summary: str | None = None,
        details: Mapping[str, Any] | None = None,
        resolved: bool = False,
    ) -> IncidentRecord:
        current = self.get_incident(incident_id)
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE incidents
                SET status = ?, attempts = ?, summary = ?, details_json = ?, resolved_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    current.attempts if attempts is None else attempts,
                    current.summary if summary is None else summary,
                    canonical_json(redact(dict(details)))
                    if details is not None
                    else canonical_json(current.details),
                    _iso(self._clock.now()) if resolved else None,
                    incident_id,
                ),
            )
        return self.get_incident(incident_id)

    def get_incident(self, incident_id: str) -> IncidentRecord:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
        if row is None:
            raise CheckpointFailed(f"incident not found: {incident_id}")
        return _incident_from_row(row)

    def list_incidents(
        self, *, start: datetime | None = None, end: datetime | None = None
    ) -> list[IncidentRecord]:
        clauses = []
        parameters: list[str] = []
        if start is not None:
            clauses.append("detected_at >= ?")
            parameters.append(_iso(start))
        if end is not None:
            clauses.append("detected_at <= ?")
            parameters.append(_iso(end))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM incidents" + where + " ORDER BY detected_at ASC",
                parameters,
            ).fetchall()
        return [_incident_from_row(row) for row in rows]

    # ------------------------------------------------------------------
    # Deployment history
    # ------------------------------------------------------------------
    def record_deployment(
        self,
        *,
        app: str,
        version: str,
        slot: str,
        status: str,
        checkpoint_id: str | None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO deployment_history
                    (created_at, app, version, slot, status, checkpoint_id, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _iso(self._clock.now()),
                    app,
                    version,
                    slot,
                    status,
                    checkpoint_id,
                    canonical_json(redact(dict(details or {}))),
                ),
            )

    def list_deployments(self, app: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as connection:
            if app is None:
                rows = connection.execute(
                    "SELECT * FROM deployment_history ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM deployment_history WHERE app = ? ORDER BY created_at DESC",
                    (app,),
                ).fetchall()
        return [
            {
                "created_at": row["created_at"],
                "app": row["app"],
                "version": row["version"],
                "slot": row["slot"],
                "status": row["status"],
                "checkpoint_id": row["checkpoint_id"],
                "details": json.loads(row["details_json"]),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Utilization / health samples (FR-082)
    # ------------------------------------------------------------------
    def record_utilization(
        self,
        *,
        host: str,
        app: str,
        cpu_percent: float,
        memory_mb: float,
        disk_percent: float,
        sampled_at: datetime | None = None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO utilization_samples
                    (sampled_at, host, app, cpu_percent, memory_mb, disk_percent)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _iso(sampled_at or self._clock.now()),
                    host,
                    app,
                    float(cpu_percent),
                    float(memory_mb),
                    float(disk_percent),
                ),
            )

    def utilization_rollup(
        self,
        *,
        start: datetime,
        end: datetime,
        host: str | None = None,
        app: str | None = None,
    ) -> UtilizationRollup:
        clauses = ["sampled_at >= ?", "sampled_at <= ?"]
        parameters: list[Any] = [_iso(start), _iso(end)]
        if host is not None:
            clauses.append("host = ?")
            parameters.append(host)
        if app is not None:
            clauses.append("app = ?")
            parameters.append(app)
        where = " AND ".join(clauses)
        with self._connection() as connection:
            row = connection.execute(
                f"""
                SELECT COUNT(*) AS count,
                       MIN(cpu_percent) AS cpu_min, AVG(cpu_percent) AS cpu_avg,
                       MAX(cpu_percent) AS cpu_max,
                       MIN(memory_mb) AS mem_min, AVG(memory_mb) AS mem_avg,
                       MAX(memory_mb) AS mem_max,
                       MIN(disk_percent) AS disk_min, AVG(disk_percent) AS disk_avg,
                       MAX(disk_percent) AS disk_max
                FROM utilization_samples WHERE {where}
                """,
                parameters,
            ).fetchone()
        if row is None or int(row["count"]) == 0:
            return UtilizationRollup(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        return UtilizationRollup(
            count=int(row["count"]),
            cpu_min=float(row["cpu_min"]),
            cpu_avg=float(row["cpu_avg"]),
            cpu_max=float(row["cpu_max"]),
            memory_mb_min=float(row["mem_min"]),
            memory_mb_avg=float(row["mem_avg"]),
            memory_mb_max=float(row["mem_max"]),
            disk_percent_min=float(row["disk_min"]),
            disk_percent_avg=float(row["disk_avg"]),
            disk_percent_max=float(row["disk_max"]),
        )

    def record_health_sample(
        self, *, service: str, healthy: bool, sampled_at: datetime | None = None
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO health_samples(sampled_at, service, healthy) VALUES (?, ?, ?)",
                (_iso(sampled_at or self._clock.now()), service, 1 if healthy else 0),
            )

    def health_summary(
        self,
        *,
        start: datetime,
        end: datetime,
        service: str | None = None,
    ) -> dict[str, Any]:
        clauses = ["sampled_at >= ?", "sampled_at <= ?"]
        parameters: list[Any] = [_iso(start), _iso(end)]
        if service is not None:
            clauses.append("service = ?")
            parameters.append(service)
        where = " AND ".join(clauses)
        with self._connection() as connection:
            row = connection.execute(
                f"""
                SELECT COUNT(*) AS total,
                       COALESCE(SUM(healthy), 0) AS healthy
                FROM health_samples WHERE {where}
                """,
                parameters,
            ).fetchone()
            services = connection.execute(
                f"SELECT DISTINCT service FROM health_samples WHERE {where} ORDER BY service",
                parameters,
            ).fetchall()
        total = int(row["total"]) if row is not None else 0
        healthy = int(row["healthy"]) if row is not None else 0
        return {
            "service": service,
            "samples": total,
            "healthy_samples": healthy,
            "uptime_percent": round((healthy / total) * 100, 2) if total else None,
            "services": [str(item["service"]) for item in services],
        }


def _incident_from_row(row: sqlite3.Row) -> IncidentRecord:
    return IncidentRecord(
        id=row["id"],
        app=row["app"],
        service=row["service"],
        status=row["status"],
        detected_at=_parse_iso(row["detected_at"]),
        resolved_at=_parse_iso(row["resolved_at"]) if row["resolved_at"] else None,
        attempts=int(row["attempts"]),
        summary=row["summary"],
        details=json.loads(row["details_json"]),
    )
