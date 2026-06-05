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

from agent_seat.registry import normalize_agent_slug

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
    """First turn after ``after_turn`` authored by ``from_agent`` (any, if None).

    Author match is alias-aware: both the stored ``from_agent`` and the expected
    value are normalized through the seat registry, so a reply posted under a
    legacy alias (``cursor``) matches a hint naming the canonical seat
    (``claude-cursor``) and vice versa — mirroring the alias-aware inbox filter
    (``recipients.expand_recipient_slugs``).
    """
    expected = normalize_agent_slug(from_agent) if from_agent is not None else None
    for t in sorted(turns, key=lambda r: r["turn_number"]):
        if t["turn_number"] <= after_turn:
            continue
        if expected is None or normalize_agent_slug(t["from_agent"]) == expected:
            return t
    return None


def build_suggested_next(
    thread_row: dict[str, Any],
    *,
    complete: bool,
    completion: Completion,
    qualifying_reply_turn: int | None,
    after_turn: int,
) -> dict[str, Any] | None:
    """Advisory payload after ``first_reply_from`` — consult *bus turn* landed.

    ``complete`` here means a new turn from the consult seat exists (usually a
    short pointer to a sidecar), NOT that findings are applied or the handoff
    arc is finished. Wording avoids overloaded "reply" (turn 1 is the packet
    pointer from dispatch; turn 2+ is the consult deliverable).
    """
    if not complete or thread_row["status"] == ThreadStatus.CLOSED:
        return None
    if completion.get("mode", "first_reply_from") != "first_reply_from":
        return None
    if qualifying_reply_turn is None:
        return None
    return {
        "phase": "consult_turn_posted",
        "pointer_turn": after_turn,
        "consult_turn": qualifying_reply_turn,
        "message": (
            f"Consult posted agent-bus turn {qualifying_reply_turn} "
            f"(turn {after_turn} was the packet pointer only). "
            "Read that turn or its sidecar path, apply agreed edits, then "
            "agent_bus(close). A bus turn is not arc completion."
        ),
        "steps": [
            {
                "action": "fetch_consult_turn",
                "tool": "agent_bus",
                "op": "fetch",
                "note": "Turn body is often a sidecar pointer, not full findings.",
            },
            {
                "action": "apply_findings",
                "tool": "fs",
                "note": "Load workspace/cortex artifact referenced on the bus.",
            },
            {
                "action": "close_handoff_thread",
                "tool": "agent_bus",
                "op": "close",
                "note": "Mandatory when nothing remains open on the bus arc.",
            },
        ],
    }


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
