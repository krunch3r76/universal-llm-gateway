"""migration_035: durable pre-paste retry attempt count on dispatch links (a:43814)."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_035"


def run(conn: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(thread_dispatch_links)").fetchall()
    }
    if "pre_paste_retry_attempts" not in columns:
        conn.execute(
            "ALTER TABLE thread_dispatch_links "
            "ADD COLUMN pre_paste_retry_attempts INTEGER NOT NULL DEFAULT 0"
        )
