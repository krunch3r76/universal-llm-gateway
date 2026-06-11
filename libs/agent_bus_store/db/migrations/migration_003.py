"""migration_003: Partial index for non-terminal dispatch-link orphan sweep."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_003"


def run(conn: sqlite3.Connection) -> None:
    """Index links awaiting terminal_status for startup reconciler."""
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dispatch_links_nonterminal "
        "ON thread_dispatch_links(linked_at) "
        "WHERE terminal_status IS NULL"
    )
