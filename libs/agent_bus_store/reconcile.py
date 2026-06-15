"""One-shot startup reconciliation for orphaned SDK dispatch links.

Delivered+read persistent generate threads are closed event-timely by
``agent_bus_store.close_on_read`` when the caller marks the on-behalf result
turn read; this sweep remains orphan-only and does not replace that path.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from .db.connection import connect
from .db.lifecycle import _transition_lifecycle_state
from .db.threads_atomic import terminate_dispatch
from .db.turns import get_turns, insert_turn
from .events.lifecycle import emit_dispatch_orphaned

# Phase 2 raises this so resume can claim links first; Phase 1 = immediate.
RECONCILE_RESUME_GRACE_S: int = int(
    os.getenv("AGENT_BUS_RECONCILE_RESUME_GRACE_S", "0")
)


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _age_seconds(linked_at: str) -> float:
    linked = _parse_ts(linked_at)
    return max(0.0, (datetime.now(UTC) - linked).total_seconds())


def _sdk_terminal_turn(thread_id: str) -> dict[str, Any] | None:
    """Return the latest cursor-sdk dispatch closeout turn, if any."""
    turns = get_turns(thread=thread_id, last=50)
    for turn in turns:
        if turn.get("from_agent") != "cursor-sdk":
            continue
        subject = turn.get("subject") or ""
        if subject.startswith("cursor-sdk dispatch"):
            return turn
    return None


def _infer_terminal_status(subject: str) -> str:
    return "failed" if "FAILED" in subject else "completed"


def _orphan_recipient(thread_id: str, caller_agent: str | None) -> str:
    if caller_agent:
        return caller_agent
    with connect() as conn:
        row = conn.execute(
            "SELECT to_agent FROM turns WHERE thread = ? "
            "ORDER BY turn_number ASC LIMIT 1",
            (thread_id,),
        ).fetchone()
    if row is not None:
        return str(row["to_agent"])
    return "dispatch"


def _reap_orphan_link(link: dict[str, Any]) -> bool:
    """Reconcile one non-terminal link. Returns True when work was done."""
    thread_id = link["thread_id"]
    execution_id = link["execution_id"]
    pipeline_id = link["pipeline_id"]
    linked_at = link["linked_at"]
    lifecycle = link["bus_lifecycle_state"]
    caller_agent = link.get("caller_agent")

    if _age_seconds(linked_at) < RECONCILE_RESUME_GRACE_S:
        return False

    sdk_turn = _sdk_terminal_turn(thread_id)
    if sdk_turn is not None:
        status = _infer_terminal_status(str(sdk_turn.get("subject") or ""))
        terminate_dispatch(
            thread_id=thread_id,
            terminal_status=status,
            execution_id=execution_id,
        )
        # The closeout turn marks the LINK terminal, but a delivered turn never
        # transitions thread lifecycle on its own, so the ephemeral thread can be
        # left in admitted/active. Drive it to the matching terminal state so
        # reconcile closes the THREAD, not just the link. Legal transitions
        # (see lifecycle._LEGAL): active->completed|failed, admitted->abandoned.
        with connect() as conn:
            row = conn.execute(
                "SELECT bus_lifecycle_state FROM threads WHERE id = ?",
                (thread_id,),
            ).fetchone()
            current = row["bus_lifecycle_state"] if row is not None else None
            if current == "active":
                _transition_lifecycle_state(
                    conn,
                    thread_id,
                    "completed" if status == "completed" else "failed",
                    "watchdog_reap",
                )
            elif current == "admitted":
                _transition_lifecycle_state(
                    conn, thread_id, "abandoned", "watchdog_reap"
                )
        return True

    with connect() as conn:
        row = conn.execute(
            "SELECT bus_lifecycle_state FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return False
        if row["bus_lifecycle_state"] not in ("admitted", "active"):
            return False
        link_row = conn.execute(
            "SELECT terminal_status FROM thread_dispatch_links "
            "WHERE thread_id = ? AND execution_id = ?",
            (thread_id, execution_id),
        ).fetchone()
        if link_row is None or link_row["terminal_status"] is not None:
            return False

    recipient = _orphan_recipient(thread_id, caller_agent)
    body = (
        "Dispatch orphaned — worker terminated before completion "
        f"(likely service restart); no terminal turn was received. "
        f"execution_id={execution_id}"
    )
    insert_turn(
        thread=thread_id,
        from_agent="dispatch",
        to_agent=recipient,
        subject="Dispatch orphaned — worker terminated before completion",
        body=body,
        after_turn=None,
    )

    emit_dispatch_orphaned(
        execution_id=execution_id,
        thread_id=thread_id,
        pipeline_id=pipeline_id,
        linked_at=linked_at,
        age_s=_age_seconds(linked_at),
    )

    terminate_dispatch(
        thread_id=thread_id,
        terminal_status="failed",
        execution_id=execution_id,
    )

    # insert_turn advances admitted→active; reap from the post-post state.
    target_state = "abandoned" if lifecycle == "admitted" else "failed"
    with connect() as conn:
        row = conn.execute(
            "SELECT bus_lifecycle_state FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if row is None or row["bus_lifecycle_state"] != "active":
            return True
        _transition_lifecycle_state(conn, thread_id, target_state, "watchdog_reap")

    return True


def reconcile_orphaned_dispatches() -> int:
    """Sweep non-terminal dispatch links on admitted/active threads.

    Idempotent: safe to run at every startup. Returns count of links reconciled.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT l.thread_id, l.execution_id, l.pipeline_id, l.linked_at, "
            "l.caller_agent, t.bus_lifecycle_state "
            "FROM thread_dispatch_links l "
            "JOIN threads t ON t.id = l.thread_id "
            "WHERE l.terminal_status IS NULL "
            "  AND t.bus_lifecycle_state IN ('admitted', 'active') "
            "ORDER BY l.linked_at ASC"
        ).fetchall()

    reaped = 0
    for row in rows:
        if _reap_orphan_link(dict(row)):
            reaped += 1
    return reaped
