"""Atomic thread + turn operations (single-transaction helpers)."""

from __future__ import annotations

from typing import Any

from .connection import connect, now
from .lifecycle import TERMINAL_STATES, _transition_lifecycle_state
from .threads import _next_auto_id, get_thread_with_links, set_thread_tags


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
    attachments: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
    lifecycle_state: str | None = None,
) -> tuple[dict[str, Any], int, str, int]:
    """Atomically create a thread and its first turn in one transaction.

    Returns (thread_detail, turn_id, created_at, turn_number).
    The thread ID is auto-assigned. Both the thread and the turn are
    committed together - no partial state is possible.

    lifecycle_state: when provided, transitions the new thread into that
    state as part of the same transaction and emits the coordination event.
    """
    from .turns import UnreadTurnsExist, _insert_attachments

    ts = now()
    with connect() as conn:
        thread_id = _next_auto_id(conn)
        conn.execute(
            "INSERT INTO threads (id, slug, summary, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread_id, slug, summary, ts, ts),
        )

        if lifecycle_state is not None:
            _transition_lifecycle_state(conn, thread_id, lifecycle_state, "create")

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

        if attachments:
            _insert_attachments(conn, turn_id, attachments)

        if tags:
            set_thread_tags(conn, thread_id, tags)

    thread_detail = get_thread_with_links(thread_id)
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
            "SELECT id, bus_lifecycle_state FROM threads WHERE id = ?", (thread_id,)
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

        # Advance lifecycle only when thread is actively managed (active → completed).
        # Other non-terminal states (pending, admitted) are not transitioned here;
        # the caller is responsible for ensuring the thread reached active first.
        lifecycle = row["bus_lifecycle_state"]
        if lifecycle == "active":
            _transition_lifecycle_state(conn, thread_id, "completed", "close")

    return get_thread_with_links(thread_id)


def admit_dispatch(
    *,
    thread_id: str,
    execution_id: str,
    pipeline_id: str,
    caller_agent: str | None = None,
) -> dict[str, Any] | None:
    """Register a dispatch link and (if pending) admit the thread.

    Returns updated thread detail with dispatch_links populated, or None
    if the thread is not found. Raises ValueError on terminal-state conflicts.

    NULL-state threads remain caller-owned: the link is registered but no
    lifecycle transition occurs (documented coverage gap — callers wanting
    recovery must pre-create with lifecycle_state="pending").
    """
    ts = now()
    with connect() as conn:
        row = conn.execute(
            "SELECT id, bus_lifecycle_state FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return None

        lifecycle = row["bus_lifecycle_state"]
        if lifecycle in TERMINAL_STATES:
            raise ValueError(
                f"Thread {thread_id!r} is in terminal state {lifecycle!r}; "
                "dispatch-admit rejected"
            )

        conn.execute(
            "INSERT OR IGNORE INTO thread_dispatch_links "
            "(thread_id, execution_id, pipeline_id, caller_agent, linked_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread_id, execution_id, pipeline_id, caller_agent, ts),
        )

        if lifecycle == "pending":
            _transition_lifecycle_state(conn, thread_id, "admitted", "admit")

    return get_thread_with_links(thread_id)
