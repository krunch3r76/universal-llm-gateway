"""migration_012: chat_url + chat_url_bound_at on dispatch links."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_012"


def run(conn: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(thread_dispatch_links)").fetchall()
    }
    if "chat_url" not in columns:
        conn.execute(
            "ALTER TABLE thread_dispatch_links "
            "ADD COLUMN chat_url TEXT"
        )
    if "chat_url_bound_at" not in columns:
        conn.execute(
            "ALTER TABLE thread_dispatch_links "
            "ADD COLUMN chat_url_bound_at TEXT"
        )
