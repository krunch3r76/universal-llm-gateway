"""migration_028: turns.from_agent_id (item-5b M1) and ledger recency index (M2)."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_028"


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(r[1]) == column for r in rows)


def run(conn: sqlite3.Connection) -> None:
    """Add nullable from_agent_id (Cursor tab uuid on post; additive)."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='turns'"
    ).fetchone():
        return
    if _has_column(conn, "turns", "from_agent_id"):
        return
    conn.execute("ALTER TABLE turns ADD COLUMN from_agent_id TEXT")
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='endpoint_wake_ledger'"
    ).fetchone():
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_endpoint_wake_thread_state_finished "
            "ON endpoint_wake_ledger(thread_id, state, finished_at)"
        )
