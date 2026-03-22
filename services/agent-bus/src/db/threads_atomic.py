"""Atomic thread + turn operations (single-transaction helpers)."""

from __future__ import annotations

from typing import Any

from src.db.connection import connect, now
from src.db.threads import _next_auto_id, get_thread


def create_thread_with_turn(
    *,
    slug: str,
    summary: str | None = None,
    from_agent: str,
    to_agent: str,
    subject: str,
    body: str,
    status: str = "open",
    after_turn: int | None = None,
) -> tuple[dict[str, Any], int, str, int]:
    """Atomically create a thread and its first turn in one transaction.

    Returns (thread_detail, turn_id, created_at, turn_number).
    The thread ID is auto-assigned. Both the thread and the turn are
    committed together — no partial state is possible.
    """
    from src.db.turns import UnreadTurnsExist

    ts = now()
    with connect() as conn:
        thread_id = _next_auto_id(conn)
        conn.execute(
            "INSERT INTO threads (id, slug, summary, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread_id, slug, summary, ts, ts),
        )

        if after_turn is not None:
            unread_rows = conn.execute(
                "SELECT id, turn_number, subject FROM turns "
                "WHERE thread = ? AND to_agent IN (?, 'all') "
                "AND turn_number > ? AND read_at IS NULL",
                (thread_id, from_agent, after_turn),
            ).fetchall()
            if unread_rows:
                raise UnreadTurnsExist([dict(r) for r in unread_rows])

        turn_number = 1
        cur = conn.execute(
            "INSERT INTO turns "
            "(thread, turn_number, from_agent, to_agent, subject, body, "
            "status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (thread_id, turn_number, from_agent, to_agent, subject, body, status, ts),
        )
        if cur.lastrowid is None:
            raise RuntimeError("Failed to insert turn: sqlite returned no row id")
        turn_id = cur.lastrowid

    thread_detail = get_thread(thread_id)
    assert thread_detail is not None
    return thread_detail, turn_id, ts, turn_number


def close_thread(
    thread_id: str,
    *,
    summary: str | None = None,
    mark_all_read: bool = True,
) -> dict[str, Any] | None:
    """Atomically close a thread: mark turns read + set status + summary.

    All mutations happen in a single SQLite transaction. Returns updated
    thread detail, or None if the thread is not found.
    """
    ts = now()
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return None

        if mark_all_read:
            conn.execute(
                "UPDATE turns SET read_at = ? WHERE thread = ? AND read_at IS NULL",
                (ts, thread_id),
            )

        sets = ["status = ?", "updated_at = ?"]
        params: list[Any] = ["closed", ts]
        if summary is not None:
            sets.append("summary = ?")
            params.append(summary)
        params.append(thread_id)
        conn.execute(f"UPDATE threads SET {', '.join(sets)} WHERE id = ?", params)

    return get_thread(thread_id)
