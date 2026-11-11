"""migration_032: durable wake-quarantine observations for contested shadow seats."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_032"


def run(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS thread_seat_wake_quarantine (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id      TEXT    NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            ledger_row_id  INTEGER NOT NULL REFERENCES thread_seat_ledger(id) ON DELETE CASCADE,
            epoch          INTEGER NOT NULL,
            created_at     TEXT    NOT NULL,
            UNIQUE (ledger_row_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_seat_wake_quarantine_thread "
        "ON thread_seat_wake_quarantine(thread_id)"
    )
