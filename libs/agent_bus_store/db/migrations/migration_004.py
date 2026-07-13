"""migration_004: Deferred liveness-probe metadata on dispatch links."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_004"


def run(conn: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(thread_dispatch_links)").fetchall()
    }
    if "liveness_probe_deferred_at" not in columns:
        conn.execute(
            "ALTER TABLE thread_dispatch_links "
            "ADD COLUMN liveness_probe_deferred_at TEXT"
        )
    if "liveness_probe_deferred_reason" not in columns:
        conn.execute(
            "ALTER TABLE thread_dispatch_links "
            "ADD COLUMN liveness_probe_deferred_reason TEXT"
        )
