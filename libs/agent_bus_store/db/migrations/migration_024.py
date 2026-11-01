"""migration_024: durable notice_closeout_key for atomic cursor-sdk CLOSEOUT dedupe."""

from __future__ import annotations

import re
import sqlite3

MIGRATION_ID = "migration_024"

_SDK_CLOSEOUT_SUBJECT_RE = re.compile(
    r"^(?:CLOSEOUT\|)?cursor-sdk CLOSEOUT (?P<dispatch_id>\S+)",
    re.IGNORECASE,
)


def _sdk_closeout_key(*, from_agent: str, subject: str) -> str | None:
    if from_agent != "cursor-sdk":
        return None
    match = _SDK_CLOSEOUT_SUBJECT_RE.match((subject or "").strip())
    if match is None:
        return None
    dispatch_id = match.group("dispatch_id").split("|", 1)[0].strip()
    if not dispatch_id:
        return None
    return f"sdk:{dispatch_id}"


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
        key = _sdk_closeout_key(
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
