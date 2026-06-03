"""Wait status derivation for the handoff result-handle wait endpoint.

Pure functions over (thread_row, turns) — no I/O, no MCP. The thread store is
the single source of truth for thread state; this module only interprets it.

Honest-status contract (decision: ship C; gpt-5.5 review): the only pre-reply
status is ``awaiting_first_reply``. We do NOT derive ``awaiting_push`` from
``read_at`` — a cooperative client-side mark_read cannot back a correctness-
bearing state machine (thread-1230 falsifier: pointer read_at stayed null while
the web seat actively processed). Push-vs-processing distinction, if ever
needed, is a server-owned ack (Phase 4), not recipient read state.

``thread_closed`` completion gates on ThreadStatus.CLOSED (the ThreadDetail
``status`` column), NEVER on any turn's TurnStatus — a closed thread can still
contain turns whose per-turn status is ``open``
(decision:agent-bus-turnstatus-not-completion-signal).
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from .turns_models import ThreadStatus

WaitStatus = Literal["awaiting_first_reply", "complete"]


class Completion(TypedDict, total=False):
    """Caller completion spec. ``mode`` selects the predicate."""

    mode: Literal["first_reply_from", "thread_closed"]
    from_agent: str  # required when mode == "first_reply_from"


def qualifying_reply(
    turns: list[dict[str, Any]],
    *,
    after_turn: int,
    from_agent: str | None,
) -> dict[str, Any] | None:
    """First turn after ``after_turn`` authored by ``from_agent`` (any, if None)."""
    for t in sorted(turns, key=lambda r: r["turn_number"]):
        if t["turn_number"] <= after_turn:
            continue
        if from_agent is None or t["from_agent"] == from_agent:
            return t
    return None


def is_complete(
    thread_row: dict[str, Any],
    turns: list[dict[str, Any]],
    *,
    after_turn: int,
    completion: Completion,
) -> bool:
    """Evaluate the caller's completion predicate against current state."""
    mode = completion.get("mode", "first_reply_from")
    if mode == "thread_closed":
        return thread_row["status"] == ThreadStatus.CLOSED
    # first_reply_from
    return (
        qualifying_reply(
            turns, after_turn=after_turn, from_agent=completion.get("from_agent")
        )
        is not None
    )


def derive_status(
    thread_row: dict[str, Any],
    turns: list[dict[str, Any]],
    *,
    after_turn: int,
    completion: Completion,
) -> WaitStatus:
    """Map current thread state to an OBSERVABLE wait status (C: two states).

    Operator push leaves no thread mutation until a reply lands, so the only
    honest pre-completion state is ``awaiting_first_reply``.
    """
    if is_complete(thread_row, turns, after_turn=after_turn, completion=completion):
        return "complete"
    return "awaiting_first_reply"
