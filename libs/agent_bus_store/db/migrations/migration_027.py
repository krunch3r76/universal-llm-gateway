"""migration_027: endpoint_wake_ledger delivery_agent_id for per-tab coalesce."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_027"


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(r[1]) == column for r in rows)


def run(conn: sqlite3.Connection) -> None:
    """Add nullable delivery_agent_id (scan-time tab key; additive)."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='endpoint_wake_ledger'"
    ).fetchone():
        return
    if _has_column(conn, "endpoint_wake_ledger", "delivery_agent_id"):
        return
    conn.execute(
        "ALTER TABLE endpoint_wake_ledger ADD COLUMN delivery_agent_id TEXT"
    )
