"""migration_001: Remove hardcoded CHECK constraints on from_agent/to_agent.

Folded from the inline _migrate_turns_drop_agent_checks in connection.py.
Agent name validation is handled by Pydantic at the API layer; hardcoded
CHECK constraints drift when new agent names are added.
"""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_001"


def run(conn: sqlite3.Connection) -> None:
    """Drop and recreate turns table without from_agent/to_agent CHECK constraints."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='turns'"
    ).fetchone()
    if row is None:
        return
    schema_sql: str = row[0]
    if "CHECK (from_agent" not in schema_sql:
        return

    conn.executescript("""
        CREATE TABLE turns_new (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            thread          TEXT NOT NULL REFERENCES threads(id),
            turn_number     INTEGER NOT NULL,
            from_agent      TEXT NOT NULL,
            to_agent        TEXT NOT NULL,
            subject         TEXT NOT NULL,
            body            TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open', 'resolved', 'superseded', 'waiting')),
            supersedes_turn INTEGER,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            read_at         TEXT,
            UNIQUE(thread, turn_number)
        );
        INSERT INTO turns_new SELECT * FROM turns;
        DROP TABLE turns;
        ALTER TABLE turns_new RENAME TO turns;
        CREATE INDEX IF NOT EXISTS idx_turns_thread
            ON turns(thread, turn_number DESC);
        CREATE INDEX IF NOT EXISTS idx_turns_to_unread
            ON turns(to_agent, read_at) WHERE read_at IS NULL;
        CREATE INDEX IF NOT EXISTS idx_turns_status
            ON turns(thread, status);
    """)
