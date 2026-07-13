"""Atomic thread + turn operations (single-transaction helpers)."""

from __future__ import annotations

from typing import Any

from .connection import connect, now
from .lifecycle import TERMINAL_STATES, _transition_lifecycle_state
from .threads import _next_auto_id, get_thread_with_links, set_thread_tags


class PendingShellContention(Exception):  # noqa: N818
    """Raised by claim_and_post_turn when the pending-empty CAS guard fails."""

    def __init__(self, thread_id: str, lifecycle: str | None, turn_count: int) -> None:
        self.thread_id = thread_id
        self.lifecycle = lifecycle
        self.turn_count = turn_count
        super().__init__(
            f"Thread {thread_id!r} pending-empty CAS failed: "
            f"lifecycle={lifecycle!r}, turn_count={turn_count}"
        )


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
    strict_slug: bool = False,
) -> tuple[dict[str, Any], int, str, int]:
    """Atomically create a thread and its first turn in one transaction.

    Returns (thread_detail, turn_id, created_at, turn_number).
    The thread ID is auto-assigned. Both the thread and the turn are
    committed together - no partial state is possible.

    lifecycle_state: when provided, transitions the new thread into that
    state as part of the same transaction and emits the coordination event.
    """
    from .turns import SlugExists, UnreadTurnsExist, _insert_attachments

    ts = now()
    with connect() as conn:
        if strict_slug:
            existing = conn.execute(
                "SELECT id FROM threads WHERE slug = ? LIMIT 1", (slug,)
            ).fetchone()
            if existing is not None:
                raise SlugExists(slug, existing["id"])

        thread_id = _next_auto_id(conn)
        conn.execute(
            "INSERT INTO threads (id, slug, summary, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread_id, slug, summary, ts, ts),
        )

        if lifecycle_state is not None:
            _transition_lifecycle_state(conn, thread_id, lifecycle_state, "create")

        if after_turn is not None:
            from ..recipients import recipient_in_clause

            include_team = from_agent != "kaywan"
            inbox_clause, inbox_params = recipient_in_clause(
                from_agent, include_team=include_team
            )
            unread_rows = conn.execute(
                f"SELECT id, turn_number, subject FROM turns "
                f"WHERE thread = ? AND {inbox_clause} "
                f"AND turn_number > ? AND read_at IS NULL",
                (thread_id, *inbox_params, after_turn),
            ).fetchall()
            if unread_rows:
                raise UnreadTurnsExist(
                    [dict(r) for r in unread_rows],
                    latest_turn_number=0,
                    provided_after_turn=after_turn,
                )

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
    lifecycle_trigger: str = "close",
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
            _transition_lifecycle_state(
                conn, thread_id, "completed", lifecycle_trigger
            )

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


def terminate_dispatch(
    *,
    thread_id: str,
    terminal_status: str,
    execution_id: str | None = None,
) -> dict[str, Any] | None:
    """Mark dispatch link(s) terminal — sets terminal_status, terminal_at, delivery_at.

    When ``execution_id`` is omitted, updates all non-terminal links for the thread
    (SDK is 1:1). Idempotent: rows already terminal are skipped via the NULL guard.
    """
    if terminal_status not in ("completed", "failed"):
        raise ValueError(
            f"terminal_status must be 'completed' or 'failed', got {terminal_status!r}"
        )

    ts = now()
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return None

        if execution_id is not None:
            conn.execute(
                "UPDATE thread_dispatch_links "
                "SET terminal_status = ?, terminal_at = ?, delivery_at = ? "
                "WHERE thread_id = ? AND execution_id = ? "
                "AND terminal_status IS NULL",
                (terminal_status, ts, ts, thread_id, execution_id),
            )
        else:
            conn.execute(
                "UPDATE thread_dispatch_links "
                "SET terminal_status = ?, terminal_at = ?, delivery_at = ? "
                "WHERE thread_id = ? AND terminal_status IS NULL",
                (terminal_status, ts, ts, thread_id),
            )

    return get_thread_with_links(thread_id)


def claim_and_post_turn(
    *,
    thread_id: str,
    execution_id: str,
    pipeline_id: str,
    caller_agent: str | None = None,
    from_agent: str,
    to_agent: str,
    subject: str,
    body: str,
) -> dict[str, Any]:
    """Atomically claim a pending-empty shell and post the first pointer turn.

    In one SQLite write transaction:
    1. Verify bus_lifecycle_state == 'pending' AND turn_count == 0; raise
       PendingShellContention on failure.
    2. Transition pending -> admitted.
    3. Insert the pointer turn (turn_number=1).
    4. Transition admitted -> active.
    5. Insert the dispatch_link row.

    Returns the updated thread detail with dispatch_links populated.
    Raises ValueError when the thread is not found.
    Raises PendingShellContention when the CAS guard fails.
    """
    ts = now()
    with connect() as conn:
        row = conn.execute(
            "SELECT bus_lifecycle_state, "
            "(SELECT COUNT(*) FROM turns WHERE thread = ?) AS turn_count "
            "FROM threads WHERE id = ?",
            (thread_id, thread_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"Thread {thread_id!r} not found")

        lifecycle: str | None = row["bus_lifecycle_state"]
        turn_count: int = int(row["turn_count"] or 0)

        if lifecycle != "pending" or turn_count != 0:
            raise PendingShellContention(thread_id, lifecycle, turn_count)

        # CAS passed: admit the thread.
        _transition_lifecycle_state(conn, thread_id, "admitted", "claim_and_post")

        # Insert the pointer turn (turn_number=1; turn_count==0 verified above).
        cur = conn.execute(
            "INSERT INTO turns "
            "(thread, turn_number, from_agent, to_agent, subject, body, "
            "status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (thread_id, 1, from_agent, to_agent, subject, body, "open", ts),
        )
        if cur.lastrowid is None:
            raise RuntimeError("claim_and_post_turn: sqlite returned no row id")

        # Transition admitted -> active (first delivery/pointer turn posted).
        _transition_lifecycle_state(conn, thread_id, "active", "claim_and_post")

        # Register the dispatch link.
        conn.execute(
            "INSERT OR IGNORE INTO thread_dispatch_links "
            "(thread_id, execution_id, pipeline_id, caller_agent, linked_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread_id, execution_id, pipeline_id, caller_agent, ts),
        )

    result = get_thread_with_links(thread_id)
    assert result is not None
    return result
