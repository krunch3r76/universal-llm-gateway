"""migration_002: Add bus_lifecycle_state column and thread_dispatch_links table."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_002"


def run(conn: sqlite3.Connection) -> None:
    """Add lifecycle state column (guarded) and dispatch links table."""
    # Guard: only ALTER if column does not yet exist.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(threads)").fetchall()}
    if "bus_lifecycle_state" not in columns:
        conn.execute(
            "ALTER TABLE threads ADD COLUMN bus_lifecycle_state TEXT "
            "CHECK (bus_lifecycle_state IS NULL OR bus_lifecycle_state IN "
            "('pending', 'admitted', 'active', 'completed', 'failed', 'abandoned'))"
        )

    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_threads_lifecycle_state
            ON threads(bus_lifecycle_state)
            WHERE bus_lifecycle_state IS NOT NULL;

        CREATE TABLE IF NOT EXISTS thread_dispatch_links (
            thread_id       TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            execution_id    TEXT NOT NULL,
            pipeline_id     TEXT NOT NULL,
            caller_agent    TEXT,
            linked_at       TEXT NOT NULL DEFAULT (datetime('now')),
            terminal_at     TEXT,
            terminal_status TEXT
                CHECK (terminal_status IS NULL OR
                       terminal_status IN ('completed', 'failed')),
            delivery_at     TEXT,
            PRIMARY KEY (thread_id, execution_id)
        );
        CREATE INDEX IF NOT EXISTS idx_dispatch_links_execution
            ON thread_dispatch_links(execution_id);
        CREATE INDEX IF NOT EXISTS idx_dispatch_links_terminal_pending
            ON thread_dispatch_links(terminal_status, delivery_at)
            WHERE terminal_status IS NOT NULL AND delivery_at IS NULL;
    """)
