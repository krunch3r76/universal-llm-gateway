"""migration_021: append-only ``house_action_journal`` for fleet action line."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_021"


def run(conn: sqlite3.Connection) -> None:
    """Create house_action_journal when absent."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='house_action_journal'"
    ).fetchone()
    if row is not None:
        return
    conn.execute(
        """
        CREATE TABLE house_action_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            root_thread_id TEXT NOT NULL REFERENCES threads(id),
            kind TEXT NOT NULL,
            actor TEXT NOT NULL,
            action_why TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_house_action_journal_root_id "
        "ON house_action_journal(root_thread_id, id)"
    )
