"""migration_018: admit terminal_status ``cancelled`` on dispatch links.

``steer=cancel_discard`` posts ``cancelled``. The migration_002 check only
allowed ``completed`` and ``failed``, so the UPDATE was rejected and the
link stayed NULL (friction a:39453).
"""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_018"

_COLUMNS = (
    "thread_id",
    "execution_id",
    "pipeline_id",
    "caller_agent",
    "linked_at",
    "terminal_at",
    "terminal_status",
    "delivery_at",
    "liveness_probe_deferred_at",
    "liveness_probe_deferred_reason",
    "archive_uri",
    "chat_url",
    "chat_url_bound_at",
)


def run(conn: sqlite3.Connection) -> None:
    """Rebuild thread_dispatch_links so the check includes ``cancelled``."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'thread_dispatch_links'"
    ).fetchone()
    if row is None:
        return
    if "cancelled" in (row[0] or ""):
        return
    column_list = ", ".join(_COLUMNS)
    conn.execute(
        """
        CREATE TABLE thread_dispatch_links_new (
            thread_id       TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            execution_id    TEXT NOT NULL,
            pipeline_id     TEXT NOT NULL,
            caller_agent    TEXT,
            linked_at       TEXT NOT NULL DEFAULT (datetime('now')),
            terminal_at     TEXT,
            terminal_status TEXT
                CHECK (terminal_status IS NULL OR
                       terminal_status IN ('completed', 'failed', 'cancelled')),
            delivery_at     TEXT,
            liveness_probe_deferred_at TEXT,
            liveness_probe_deferred_reason TEXT,
            archive_uri     TEXT,
            chat_url        TEXT,
            chat_url_bound_at TEXT,
            PRIMARY KEY (thread_id, execution_id)
        )
        """
    )
    conn.execute(
        "INSERT INTO thread_dispatch_links_new ("
        f"{column_list}) SELECT {column_list} FROM thread_dispatch_links"
    )
    conn.execute("DROP TABLE thread_dispatch_links")
    conn.execute(
        "ALTER TABLE thread_dispatch_links_new RENAME TO thread_dispatch_links"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dispatch_links_execution "
        "ON thread_dispatch_links(execution_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dispatch_links_terminal_pending "
        "ON thread_dispatch_links(terminal_status, delivery_at) "
        "WHERE terminal_status IS NOT NULL AND delivery_at IS NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dispatch_links_nonterminal "
        "ON thread_dispatch_links(linked_at) "
        "WHERE terminal_status IS NULL"
    )
