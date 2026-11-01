"""migration_024: durable notice_closeout_key for atomic cursor-sdk CLOSEOUT dedupe."""

from __future__ import annotations

import sqlite3

from agent_bus_store.notice_closeout_sdk_key import sdk_notice_closeout_key

MIGRATION_ID = "migration_024"


def run(conn: sqlite3.Connection) -> None:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(turns)")]
    if "notice_closeout_key" not in cols:
        conn.execute("ALTER TABLE turns ADD COLUMN notice_closeout_key TEXT")

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_turns_notice_closeout_dedupe "
        "ON turns(thread, from_agent, notice_closeout_key) "
        "WHERE notice_closeout_key IS NOT NULL"
    )

    rows = conn.execute(
        "SELECT id, thread, from_agent, subject FROM turns "
        "WHERE notice_closeout_key IS NULL"
    ).fetchall()
    for row in rows:
        key = sdk_notice_closeout_key(
            from_agent=str(row["from_agent"]),
            subject=str(row["subject"]),
        )
        if key is None:
            continue
        occupied = conn.execute(
            "SELECT 1 FROM turns WHERE thread=? AND from_agent=? "
            "AND notice_closeout_key=? LIMIT 1",
            (str(row["thread"]), str(row["from_agent"]), key),
        ).fetchone()
        if occupied is not None:
            continue
        conn.execute(
            "UPDATE turns SET notice_closeout_key=? WHERE id=? AND notice_closeout_key IS NULL",
            (key, row["id"]),
        )
