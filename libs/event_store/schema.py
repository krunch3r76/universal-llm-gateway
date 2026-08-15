"""SQLite schema and migrations for the event store.

The store imports this module during startup to create indexed event tables and
to add correlation columns to databases created by older event-service builds.
"""

from __future__ import annotations

import sqlite3

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    signal TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'observation',
    scope TEXT NOT NULL DEFAULT 'global',
    ts_unix_ms INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    source TEXT NOT NULL,
    request_id TEXT GENERATED ALWAYS AS (json_extract(payload, '$.request_id')) VIRTUAL,
    execution_id TEXT GENERATED ALWAYS AS (json_extract(payload, '$.execution_id')) VIRTUAL,
    model_id TEXT GENERATED ALWAYS AS (json_extract(payload, '$.model_id')) VIRTUAL,
    gateway_id TEXT GENERATED ALWAYS AS (json_extract(payload, '$.gateway_id')) VIRTUAL,
    payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_signal_ts ON events(signal, ts_unix_ms);
CREATE INDEX IF NOT EXISTS idx_request_id ON events(request_id) WHERE request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_execution_id ON events(execution_id) WHERE execution_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_role_scope_ts ON events(role, scope, ts_unix_ms);
CREATE INDEX IF NOT EXISTS idx_source_ts ON events(source, ts_unix_ms);
CREATE INDEX IF NOT EXISTS idx_model_id ON events(model_id) WHERE model_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_seq ON events(seq DESC);
CREATE INDEX IF NOT EXISTS idx_ts_unix_ms ON events(ts_unix_ms DESC);

CREATE TABLE IF NOT EXISTS request_snapshots (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    ts_unix_ms INTEGER NOT NULL,
    model_id TEXT,
    gateway_id TEXT,
    payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_snap_request ON request_snapshots(request_id, phase);
CREATE INDEX IF NOT EXISTS idx_snap_ts ON request_snapshots(ts_unix_ms);
CREATE INDEX IF NOT EXISTS idx_snap_model ON request_snapshots(model_id) WHERE model_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL,
    baseline_id TEXT,
    metrics TEXT,
    verdict TEXT,
    ts_unix_ms INTEGER NOT NULL
);
"""

_CORRELATION_TAXONOMY_COLUMNS: tuple[tuple[str, str], ...] = (
    (
        "thread_id",
        "TEXT GENERATED ALWAYS AS (json_extract(payload, '$.thread_id')) VIRTUAL",
    ),
    (
        "dispatch_id",
        "TEXT GENERATED ALWAYS AS (json_extract(payload, '$.dispatch_id')) VIRTUAL",
    ),
    (
        "failure_layer",
        "TEXT GENERATED ALWAYS AS (json_extract(payload, '$.failure_layer')) VIRTUAL",
    ),
    (
        "transport_error_kind",
        "TEXT GENERATED ALWAYS AS (json_extract(payload, '$.transport_error_kind')) VIRTUAL",
    ),
    (
        "http_status",
        "INTEGER GENERATED ALWAYS AS (json_extract(payload, '$.http_status')) VIRTUAL",
    ),
    (
        "worker_error_code",
        "TEXT GENERATED ALWAYS AS (json_extract(payload, '$.worker_error_code')) VIRTUAL",
    ),
)

_CORRELATION_TAXONOMY_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_thread_id ON events(thread_id) WHERE thread_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_dispatch_id ON events(dispatch_id) WHERE dispatch_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_failure_layer_ts ON events(failure_layer, ts_unix_ms)",
    "CREATE INDEX IF NOT EXISTS idx_transport_error_kind_ts ON events(transport_error_kind, ts_unix_ms)",
    "CREATE INDEX IF NOT EXISTS idx_http_status_ts ON events(http_status, ts_unix_ms)",
    "CREATE INDEX IF NOT EXISTS idx_worker_error_code_ts ON events(worker_error_code, ts_unix_ms)",
)


def migrate_correlation_taxonomy_columns(db: sqlite3.Connection) -> None:
    """Add correlation projections and indexes to an existing event database."""
    existing = {
        str(row[1])
        for pragma in ("table_xinfo", "table_info")
        for row in _read_columns(db, pragma)
    }
    for name, definition in _CORRELATION_TAXONOMY_COLUMNS:
        if name in existing:
            continue
        try:
            db.execute(f"ALTER TABLE events ADD COLUMN {name} {definition}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
    for ddl in _CORRELATION_TAXONOMY_INDEXES:
        db.execute(ddl)


def _read_columns(db: sqlite3.Connection, pragma: str) -> list[sqlite3.Row]:
    """Read schema columns, tolerating SQLite versions without table_xinfo."""
    try:
        return db.execute(f"PRAGMA {pragma}(events)").fetchall()
    except sqlite3.OperationalError:
        return []
