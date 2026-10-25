"""migration_020: ``decided_by_rule`` on endpoint_wake_ledger for declared-map audit."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_020"


def run(conn: sqlite3.Connection) -> None:
    """Add decided_by_rule when absent."""
    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(endpoint_wake_ledger)")
    }
    if "decided_by_rule" in cols:
        return
    conn.execute(
        "ALTER TABLE endpoint_wake_ledger ADD COLUMN decided_by_rule TEXT"
    )
