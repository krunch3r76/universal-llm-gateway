"""migration_015: turns.attested_by and turns.user_agent for credential stamps."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_015"


def run(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(turns)").fetchall()}
    if "attested_by" not in columns:
        conn.execute("ALTER TABLE turns ADD COLUMN attested_by TEXT")
    if "user_agent" not in columns:
        conn.execute("ALTER TABLE turns ADD COLUMN user_agent TEXT")
