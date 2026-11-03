from __future__ import annotations

import pytest

from checkpoint_store.store import CheckpointStore
from common.errors import CheckpointFailed, CheckpointIntegrityError


def test_snapshot_and_get_round_trip(store: CheckpointStore) -> None:
    state = {"service": {"name": "demo", "status": "Running"}, "files": ["a", "b"]}
    checkpoint_id = store.snapshot("pre-deploy", state, metadata={"app": "demo"})
    checkpoint = store.get(checkpoint_id)
    assert checkpoint.state == state
    assert checkpoint.label == "pre-deploy"
    assert checkpoint.metadata == {"app": "demo"}
    assert len(checkpoint.checksum) == 64


def test_restore_calls_restorer_with_identical_state(store: CheckpointStore) -> None:
    state = {"nested": {"value": 42}, "list": [1, 2, 3]}
    checkpoint_id = store.snapshot("test", state)
    restored: list[dict] = []
    returned = store.restore(checkpoint_id, restorer=restored.append)
    assert restored == [state]
    assert returned == state


def test_checksum_tampering_is_detected(store: CheckpointStore) -> None:
    checkpoint_id = store.snapshot("test", {"a": 1})
    with store._connection() as connection:
        connection.execute(
            "UPDATE checkpoints SET state_json = ? WHERE id = ?",
            ('{"a":2}', checkpoint_id),
        )
    with pytest.raises(CheckpointIntegrityError):
        store.get(checkpoint_id)


def test_missing_checkpoint_raises(store: CheckpointStore) -> None:
    with pytest.raises(CheckpointFailed):
        store.get("ckpt-missing")


def test_restorer_failure_is_reported_and_recorded(store: CheckpointStore) -> None:
    checkpoint_id = store.snapshot("test", {"a": 1})

    def fail(_: dict) -> None:
        raise RuntimeError("boom")

    with pytest.raises(CheckpointFailed):
        store.restore(checkpoint_id, restorer=fail)


def test_checkpoint_ids_are_unique(store: CheckpointStore) -> None:
    first = store.snapshot("x", {"a": 1})
    second = store.snapshot("x", {"a": 2})
    assert first != second
    assert len(store.list_checkpoints()) == 2


def test_backup_export(store: CheckpointStore, tmp_path) -> None:
    store.snapshot("one", {"a": 1})
    backup = store.export_backup(tmp_path / "backup.db")
    assert backup.exists()
    copied = CheckpointStore(backup)
    assert len(copied.list_checkpoints()) == 1


def test_incident_lifecycle(store: CheckpointStore) -> None:
    incident = store.create_incident(
        app="demo",
        service="demo-svc",
        summary="service stopped",
        details={"pid": 42},
        incident_id="inc-1",
    )
    assert incident.status == "Detected"
    updated = store.update_incident(
        "inc-1", status="Resolved", attempts=2, resolved=True
    )
    assert updated.status == "Resolved"
    assert updated.attempts == 2
    assert updated.resolved_at is not None
    assert store.list_incidents()[0].id == "inc-1"


def test_utilization_rollup(store: CheckpointStore, clock) -> None:
    for value in (10.0, 20.0, 30.0):
        store.record_utilization(
            host="host1", app="demo", cpu_percent=value, memory_mb=100 + value, disk_percent=50
        )
        clock.advance(60)
    rollup = store.utilization_rollup(
        start=clock.now().replace(hour=0), end=clock.now()
    )
    assert rollup.count == 3
    assert rollup.cpu_min == 10.0
    assert rollup.cpu_avg == 20.0
    assert rollup.cpu_max == 30.0
    assert rollup.to_dict()["cpu_percent"]["avg"] == 20.0


def test_deployment_history(store: CheckpointStore) -> None:
    store.record_deployment(
        app="demo",
        version="1.0",
        slot="blue",
        status="success",
        checkpoint_id="ckpt-1",
        details={"health": True},
    )
    records = store.list_deployments("demo")
    assert records[0]["version"] == "1.0"
    assert records[0]["details"] == {"health": True}
