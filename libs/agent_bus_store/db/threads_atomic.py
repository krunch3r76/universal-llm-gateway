"""Atomic thread + turn operations (single-transaction helpers)."""

from __future__ import annotations

from typing import Any

from .connection import connect, now
from .lane_associations import associate_lane, get_current_lane
from .lifecycle import TERMINAL_STATES, _transition_lifecycle_state
from .lineage import get_thread_lineage
from .threads import (
    _next_auto_id,
    get_thread,
    get_thread_with_links,
    normalize_thread_id,
    set_thread_tags,
)


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
    body_transformer: Any | None = None,
    enroll_charter_runner: bool = False,
) -> tuple[dict[str, Any], int, str, int]:
    """Atomically create a thread and its first turn in one transaction.

    Returns (thread_detail, turn_id, created_at, turn_number).
    The thread ID is auto-assigned. Both the thread and the turn are
    committed together - no partial state is possible.

    lifecycle_state: when provided, transitions the new thread into that
    state as part of the same transaction and emits the coordination event.

    body_transformer: optional ``(thread_id: str) -> str`` called after the
    thread row is inserted and before the turn insert (same transaction).
    Used for soft-spill so a raise rolls back the thread (no orphan).
    """
    from agent_bus_store.thread_classification import gate_thread_tags

    from .turns import SlugExists, UnreadTurnsExist, _insert_attachments

    gated_tags = gate_thread_tags(
        tags, prior_tags=[], enroll_charter_runner=enroll_charter_runner
    )
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

        insert_body = body_transformer(thread_id) if body_transformer else body

        turn_number = 1
        cur = conn.execute(
            "INSERT INTO turns "
            "(thread, turn_number, from_agent, to_agent, subject, body, "
            "status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                thread_id,
                turn_number,
                from_agent,
                to_agent,
                subject,
                insert_body,
                status,
                ts,
            ),
        )
        if cur.lastrowid is None:
            raise RuntimeError("Failed to insert turn: sqlite returned no row id")
        turn_id = cur.lastrowid

        if attachments:
            _insert_attachments(conn, turn_id, attachments)

        if gated_tags:
            set_thread_tags(conn, thread_id, gated_tags)

    from ..events.turn_created import emit_turn_created

    emit_turn_created(
        thread=thread_id,
        turn_id=turn_id,
        turn_number=turn_number,
        from_agent=from_agent,
        to_agent=to_agent,
        subject=subject,
        created_at=ts,
    )
    from claude_bundles.cse_session_obligations import maybe_mirror_protocol_turn

    maybe_mirror_protocol_turn(
        thread=thread_id,
        turn_id=turn_id,
        turn_number=turn_number,
        created_at=ts,
        body=insert_body,
    )
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

    When the reserved ``charter-runner`` enrollment tag is present, it is
    stripped in the same transaction and ``manage.charter.tick.root_closed``
    is emitted so the dispatch-board fold can leave ``parked``.
    """
    from agent_bus_store.enrollment_guard import ENROLLMENT_TAG

    from .threads import _load_thread_tags

    ts = now()
    stripped_enrollment = False
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

        prior_tags = _load_thread_tags(conn, [thread_id]).get(thread_id, [])
        if ENROLLMENT_TAG in prior_tags:
            set_thread_tags(
                conn,
                thread_id,
                [t for t in prior_tags if t != ENROLLMENT_TAG],
            )
            stripped_enrollment = True

        # Advance lifecycle only when thread is actively managed (active → completed).
        # Other non-terminal states (pending, admitted) are not transitioned here;
        # the caller is responsible for ensuring the thread reached active first.
        lifecycle = row["bus_lifecycle_state"]
        if lifecycle == "active":
            _transition_lifecycle_state(
                conn, thread_id, "completed", lifecycle_trigger
            )

    detail = get_thread_with_links(thread_id)
    # Observation: CLI / direct HTTP /close previously emitted nothing when
    # bus_lifecycle_state was NULL (standing roots). MCP close also records;
    # dual emit is acceptable for this low-frequency lifecycle edge.
    if detail is not None:
        from ..events.thread_closed import (
            emit_charter_root_closed_on_unenroll,
            emit_persistent_thread_closed,
            emit_thread_closed,
        )

        via = None if lifecycle_trigger == "close" else lifecycle_trigger
        emit_thread_closed(thread_id, via=via)
        if "bus_lifecycle:persistent" in (detail.get("tags") or []):
            emit_persistent_thread_closed(thread_id, via=via)
        if stripped_enrollment:
            emit_charter_root_closed_on_unenroll(
                root=thread_id,
                reason="close_while_enrolled",
            )
    return detail


def _insert_dispatch_link_row(
    conn,
    *,
    thread_id: str,
    execution_id: str,
    pipeline_id: str,
    caller_agent: str | None,
    linked_at: str,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO thread_dispatch_links "
        "(thread_id, execution_id, pipeline_id, caller_agent, linked_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (thread_id, execution_id, pipeline_id, caller_agent, linked_at),
    )


def _maybe_mirror_dispatch_to_parent(
    *,
    worker_thread_id: str,
    parent_thread_id: str | None,
    execution_id: str,
    pipeline_id: str,
    caller_agent: str | None,
    linked_at: str,
    conn,
) -> None:
    """Dual-write dispatch link to parent and lane-bind worker when distinct (G8)."""
    if parent_thread_id is None:
        return
    worker_thread_id = normalize_thread_id(worker_thread_id)
    parent_thread_id = normalize_thread_id(parent_thread_id)
    if parent_thread_id == worker_thread_id:
        return
    if get_thread(parent_thread_id) is None:
        raise ValueError(
            f"parent_thread_id {parent_thread_id!r} not found; "
            "dispatch-admit parent mirror rejected"
        )
    _insert_dispatch_link_row(
        conn,
        thread_id=parent_thread_id,
        execution_id=execution_id,
        pipeline_id=pipeline_id,
        caller_agent=caller_agent,
        linked_at=linked_at,
    )


def _maybe_lane_bind_worker_to_parent(
    *,
    worker_thread_id: str,
    parent_thread_id: str | None,
    execution_id: str,
    caller_agent: str | None,
) -> None:
    """Bind worker→parent with sub_mission when worker has no lane yet (G8)."""
    if parent_thread_id is None:
        return
    worker_thread_id = normalize_thread_id(worker_thread_id)
    parent_thread_id = normalize_thread_id(parent_thread_id)
    if parent_thread_id == worker_thread_id:
        return
    current = get_current_lane(thread_id=worker_thread_id)
    if current.get("state") != "none":
        return
    associate_lane(
        thread_id=worker_thread_id,
        parent_thread_id=parent_thread_id,
        lane_role="sub_mission",
        bound_by=caller_agent,
        evidence=f"dispatch-admit:{execution_id}",
    )


def backfill_parent_facing_dispatch_enumeration(
    *,
    worker_thread_id: str,
    parent_thread_id: str,
    execution_id: str | None = None,
    pipeline_id: str | None = None,
    caller_agent: str | None = None,
) -> dict[str, Any]:
    """Idempotent G8 backfill: mirror worker dispatch link onto parent + lane_bind."""
    worker_thread_id = normalize_thread_id(worker_thread_id)
    parent_thread_id = normalize_thread_id(parent_thread_id)
    if get_thread(worker_thread_id) is None:
        raise ValueError(f"worker thread {worker_thread_id!r} not found")
    if get_thread(parent_thread_id) is None:
        raise ValueError(f"parent thread {parent_thread_id!r} not found")

    ts = now()
    with connect() as conn:
        if execution_id is None:
            row = conn.execute(
                "SELECT execution_id, pipeline_id, caller_agent "
                "FROM thread_dispatch_links WHERE thread_id = ? "
                "ORDER BY linked_at ASC LIMIT 1",
                (worker_thread_id,),
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"worker thread {worker_thread_id!r} has no dispatch links"
                )
            execution_id = row["execution_id"]
            pipeline_id = pipeline_id or row["pipeline_id"]
            caller_agent = caller_agent if caller_agent is not None else row["caller_agent"]
        else:
            if pipeline_id is None:
                row = conn.execute(
                    "SELECT pipeline_id, caller_agent FROM thread_dispatch_links "
                    "WHERE thread_id = ? AND execution_id = ?",
                    (worker_thread_id, execution_id),
                ).fetchone()
                if row is not None:
                    pipeline_id = pipeline_id or row["pipeline_id"]
                    if caller_agent is None:
                        caller_agent = row["caller_agent"]
            if pipeline_id is None:
                pipeline_id = "cursor-sdk-generate"

        _insert_dispatch_link_row(
            conn,
            thread_id=worker_thread_id,
            execution_id=execution_id,
            pipeline_id=pipeline_id,
            caller_agent=caller_agent,
            linked_at=ts,
        )
        _maybe_mirror_dispatch_to_parent(
            worker_thread_id=worker_thread_id,
            parent_thread_id=parent_thread_id,
            execution_id=execution_id,
            pipeline_id=pipeline_id,
            caller_agent=caller_agent,
            linked_at=ts,
            conn=conn,
        )

    _maybe_lane_bind_worker_to_parent(
        worker_thread_id=worker_thread_id,
        parent_thread_id=parent_thread_id,
        execution_id=execution_id,
        caller_agent=caller_agent,
    )
    lineage = get_thread_lineage(parent_thread_id)
    return {
        "worker_thread_id": worker_thread_id,
        "parent_thread_id": parent_thread_id,
        "execution_id": execution_id,
        "parent_child_count": len(lineage.children) if lineage else 0,
        "parent_dispatch_link_count": len(lineage.dispatch_links) if lineage else 0,
    }


def admit_dispatch(
    *,
    thread_id: str,
    execution_id: str,
    pipeline_id: str,
    caller_agent: str | None = None,
    parent_thread_id: str | None = None,
) -> dict[str, Any] | None:
    """Register a dispatch link and (if pending) admit the thread.

    When ``parent_thread_id`` is set and distinct from ``thread_id``, also
    mirrors the dispatch link onto the parent and lane-binds the worker (G8).

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

        _insert_dispatch_link_row(
            conn,
            thread_id=thread_id,
            execution_id=execution_id,
            pipeline_id=pipeline_id,
            caller_agent=caller_agent,
            linked_at=ts,
        )
        _maybe_mirror_dispatch_to_parent(
            worker_thread_id=thread_id,
            parent_thread_id=parent_thread_id,
            execution_id=execution_id,
            pipeline_id=pipeline_id,
            caller_agent=caller_agent,
            linked_at=ts,
            conn=conn,
        )

        if lifecycle == "pending":
            _transition_lifecycle_state(conn, thread_id, "admitted", "admit")

    _maybe_lane_bind_worker_to_parent(
        worker_thread_id=thread_id,
        parent_thread_id=parent_thread_id,
        execution_id=execution_id,
        caller_agent=caller_agent,
    )
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
    parent_thread_id: str | None = None,
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
        turn_id = cur.lastrowid

        # Transition admitted -> active (first delivery/pointer turn posted).
        _transition_lifecycle_state(conn, thread_id, "active", "claim_and_post")

        # Register the dispatch link (worker + optional parent mirror — G8).
        _insert_dispatch_link_row(
            conn,
            thread_id=thread_id,
            execution_id=execution_id,
            pipeline_id=pipeline_id,
            caller_agent=caller_agent,
            linked_at=ts,
        )
        _maybe_mirror_dispatch_to_parent(
            worker_thread_id=thread_id,
            parent_thread_id=parent_thread_id,
            execution_id=execution_id,
            pipeline_id=pipeline_id,
            caller_agent=caller_agent,
            linked_at=ts,
            conn=conn,
        )

    _maybe_lane_bind_worker_to_parent(
        worker_thread_id=thread_id,
        parent_thread_id=parent_thread_id,
        execution_id=execution_id,
        caller_agent=caller_agent,
    )

    from ..events.turn_created import emit_turn_created

    emit_turn_created(
        thread=thread_id,
        turn_id=turn_id,
        turn_number=1,
        from_agent=from_agent,
        to_agent=to_agent,
        subject=subject,
        created_at=ts,
    )
    from claude_bundles.cse_session_obligations import maybe_mirror_protocol_turn

    maybe_mirror_protocol_turn(
        thread=thread_id,
        turn_id=turn_id,
        turn_number=1,
        created_at=ts,
        body=body,
    )
    result = get_thread_with_links(thread_id)
    assert result is not None
    return result
