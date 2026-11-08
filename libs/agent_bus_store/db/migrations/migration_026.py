"""migration_026: endpoint_wake_ledger wake attempt telemetry columns."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_026"


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(r[1]) == column for r in rows)


def run(conn: sqlite3.Connection) -> None:
    """Add nullable wake telemetry columns (additive; no rebuild)."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='endpoint_wake_ledger'"
    ).fetchone():
        return
    specs = (
        ("wake_last_attempt_at", "TEXT"),
        ("wake_next_attempt_at", "TEXT"),
        ("wake_attempt_count", "INTEGER DEFAULT 0"),
        ("delivery_failure_detail", "TEXT"),
    )
    for col, ddl in specs:
        if _has_column(conn, "endpoint_wake_ledger", col):
            continue
        conn.execute(f"ALTER TABLE endpoint_wake_ledger ADD COLUMN {col} {ddl}")
