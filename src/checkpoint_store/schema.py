"""SQLite schema shared by the state store, approval gate, and reporting.

ADR-006: SQLite is the MVP backing store. All access goes through parameterized
queries; no component builds SQL from user input.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    label TEXT NOT NULL,
    state_json TEXT NOT NULL,
    checksum TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_created
    ON checkpoints(created_at DESC);

CREATE TABLE IF NOT EXISTS restore_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checkpoint_id TEXT NOT NULL,
    restored_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    success INTEGER NOT NULL,
    detail TEXT NOT NULL,
    FOREIGN KEY (checkpoint_id) REFERENCES checkpoints(id)
);

CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    risk_categories_json TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    note TEXT NOT NULL DEFAULT '',
    rollback_plan TEXT NOT NULL DEFAULT '',
    used_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_approvals_status
    ON approval_requests(status, requested_at DESC);

CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    app TEXT NOT NULL,
    service TEXT NOT NULL,
    status TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    resolved_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL,
    details_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_incidents_detected
    ON incidents(detected_at DESC);

CREATE TABLE IF NOT EXISTS deployment_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    app TEXT NOT NULL,
    version TEXT NOT NULL,
    slot TEXT NOT NULL,
    status TEXT NOT NULL,
    checkpoint_id TEXT,
    details_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_deployments_app
    ON deployment_history(app, created_at DESC);

CREATE TABLE IF NOT EXISTS utilization_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sampled_at TEXT NOT NULL,
    host TEXT NOT NULL,
    app TEXT NOT NULL,
    cpu_percent REAL NOT NULL,
    memory_mb REAL NOT NULL,
    disk_percent REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_utilization_lookup
    ON utilization_samples(host, app, sampled_at);

CREATE TABLE IF NOT EXISTS health_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sampled_at TEXT NOT NULL,
    service TEXT NOT NULL,
    healthy INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_health_lookup
    ON health_samples(service, sampled_at);
"""


def connect_sql(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
