"""migration_011: archive_uri on dispatch links for failed delivery harvest."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_011"


def run(conn: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(thread_dispatch_links)").fetchall()
    }
    if "archive_uri" not in columns:
        conn.execute(
            "ALTER TABLE thread_dispatch_links "
            "ADD COLUMN archive_uri TEXT"
        )
