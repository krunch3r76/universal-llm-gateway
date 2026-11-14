"""migration_037: endpoint_wake_attempt shadow telemetry + S1 ledger columns (a:43821 S1)."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_037"

_LEDGER_COLS = (
    ("enqueued_at", "TEXT"),
    ("expected_delivery_at", "TEXT"),
    ("delivered_at", "TEXT"),
    ("dropped_at", "TEXT"),
    ("drop_reason", "TEXT"),
    ("last_failure_reason", "TEXT"),
    ("surfaced_at", "TEXT"),
)


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(r[1]) == column for r in rows)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def run(conn: sqlite3.Connection) -> None:
    if not _has_table(conn, "endpoint_wake_ledger"):
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS endpoint_wake_attempt (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wake_row_id INTEGER NOT NULL REFERENCES endpoint_wake_ledger(id) ON DELETE CASCADE,
            attempt_no INTEGER NOT NULL,
            channel TEXT CHECK (
                channel IS NULL OR channel IN (
                    'glass_queue', 'glass_submit', 'dom_paste', 'relay_pointer'
                )
            ),
            gate_generating INTEGER,
            gate_draft INTEGER,
            gate_run_state TEXT,
            mid_stream INTEGER,
            outcome TEXT NOT NULL,
            reason_code TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            UNIQUE(wake_row_id, attempt_no)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_endpoint_wake_attempt_row "
        "ON endpoint_wake_attempt(wake_row_id)"
    )
    for col, ddl in _LEDGER_COLS:
        if _has_column(conn, "endpoint_wake_ledger", col):
            continue
        conn.execute(f"ALTER TABLE endpoint_wake_ledger ADD COLUMN {col} {ddl}")
    if _has_column(conn, "endpoint_wake_ledger", "enqueued_at"):
        conn.execute(
            "UPDATE endpoint_wake_ledger SET enqueued_at=created_at "
            "WHERE enqueued_at IS NULL"
        )
