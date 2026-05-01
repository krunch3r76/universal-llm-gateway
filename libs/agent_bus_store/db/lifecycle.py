"""Lifecycle state machine for agent-bus threads.

Kept in a standalone module so both threads_atomic.py and turns.py can
import without creating circular dependencies.
"""

from __future__ import annotations

import sqlite3

from ..events.lifecycle import emit_lifecycle_transitioned, emit_thread_reopened
from .connection import now

TERMINAL_STATES: frozenset[str] = frozenset({"completed", "failed", "abandoned"})

# Legal (from_state, to_state) pairs; None represents a lifecycle-unset thread.
_LEGAL: frozenset[tuple[str | None, str]] = frozenset(
    [
        (None, "pending"),  # create with intent
        (None, "active"),  # create_thread_with_turn (has turn from inception)
        ("pending", "admitted"),  # dispatch-admit received
        ("pending", "abandoned"),  # TTL expired with no admit (watchdog)
        ("admitted", "active"),  # first delivery turn posted
        ("admitted", "abandoned"),  # watchdog reap
        ("active", "completed"),  # close_thread on lifecycle-managed thread
        ("active", "failed"),  # delivery.failed, all dispatches failed
        ("active", "abandoned"),  # watchdog reap — no delivery after terminal
        ("completed", "active"),  # re-open via turn POST
        ("abandoned", "active"),  # re-open via turn POST
        ("failed", "active"),  # re-open via turn POST (consistent with Q5 revision)
    ]
)


def _transition_lifecycle_state(
    conn: sqlite3.Connection,
    thread_id: str,
    to_state: str,
    trigger: str,
) -> None:
    """Atomically update bus_lifecycle_state; caller holds the transaction.

    Validates the transition against the legal-transitions table and raises
    ValueError on illegal transitions. Emits lifecycle events from here —
    single point of correctness so call-sites cannot drift.
    """
    row = conn.execute(
        "SELECT bus_lifecycle_state FROM threads WHERE id = ?", (thread_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Thread {thread_id!r} not found")

    from_state: str | None = row["bus_lifecycle_state"]

    if (from_state, to_state) not in _LEGAL:
        raise ValueError(
            f"Illegal lifecycle transition for thread {thread_id!r}: "
            f"{from_state!r} → {to_state!r}"
        )

    # Bump updated_at on every transition — TTL clocks in the watchdog anchor to
    # this field, so every state change must reset it regardless of the caller.
    ts = now()
    conn.execute(
        "UPDATE threads SET bus_lifecycle_state = ?, updated_at = ? WHERE id = ?",
        (to_state, ts, thread_id),
    )

    # Emit coordination event — single point of correctness, never at call-sites.
    emit_lifecycle_transitioned(
        thread=thread_id,
        from_state=from_state,
        to_state=to_state,
        trigger=trigger,  # type: ignore[arg-type]
    )

    # Also emit the re-open signal when transitioning out of a terminal state.
    if from_state in TERMINAL_STATES and to_state == "active":
        emit_thread_reopened(
            thread=thread_id,
            from_state=from_state,
            to_state=to_state,
        )
