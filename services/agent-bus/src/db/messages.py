"""Legacy message CRUD (pre-turns API)."""

from __future__ import annotations

from typing import Any

from src.db.connection import connect, now


def insert_message(
    from_agent: str, to_agent: str, thread: str, body: str
) -> tuple[int, str]:
    ts = now()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO messages (from_agent, to_agent, thread, body, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (from_agent, to_agent, thread, body, ts),
        )
        if cur.lastrowid is None:
            raise RuntimeError("Failed to insert message: sqlite returned no row id")
        return cur.lastrowid, ts


def get_messages(
    to: str,
    thread: str | None = None,
    since: int | None = None,
    unread: bool = False,
) -> list[dict[str, Any]]:
    clauses = ["(to_agent = ? OR to_agent = 'all')"]
    params: list[Any] = [to]

    if thread is not None:
        clauses.append("thread = ?")
        params.append(thread)
    if since is not None:
        clauses.append("id > ?")
        params.append(since)
    if unread:
        clauses.append("read = 0")

    sql = f"SELECT * FROM messages WHERE {' AND '.join(clauses)} ORDER BY id"
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def mark_read(message_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("UPDATE messages SET read = 1 WHERE id = ?", (message_id,))
        return cur.rowcount > 0


def list_threads() -> list[dict[str, Any]]:
    sql = """\
    SELECT
        thread,
        COUNT(*) AS total,
        SUM(CASE WHEN read = 0 THEN 1 ELSE 0 END) AS unread,
        MAX(timestamp) AS latest
    FROM messages
    GROUP BY thread
    ORDER BY latest DESC
    """
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql).fetchall()]
