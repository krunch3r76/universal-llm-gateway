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

from agent_seat.registry import normalize_bus_address

from .close_on_read import CLOSE_ON_READ_TAG
from .disposition import first_line_is_disposition_type, resolve_bus_lifecycle
from .turns_models import ThreadStatus

WaitStatus = Literal["awaiting_first_reply", "complete"]

# Terminal Auto-orchestrator status tokens (agent_bus.request completion).
STATUS_COMPLETION_MODES: frozenset[str] = frozenset(
    {
        "status:done",
        "status:failed",
        "status:needs-attended",
        "status:superseded",
    }
)
CompletionMode = Literal[
    "first_reply_from",
    "thread_closed",
    "status:done",
    "status:failed",
    "status:needs-attended",
    "status:superseded",
]


class Completion(TypedDict, total=False):
    """Caller completion spec. ``mode`` selects the predicate."""

    mode: CompletionMode
    from_agent: str  # required when mode == "first_reply_from"


def _turn_carries_status_token(turn: dict[str, Any], token: str) -> bool:
    """True when subject or body contains the exact ``status:…`` token."""
    needle = token if token.startswith("status:") else f"status:{token}"
    subject = str(turn.get("subject") or "")
    body = str(turn.get("body") or "")
    return needle in subject or needle in body


def qualifying_status_turn(
    turns: list[dict[str, Any]],
    *,
    after_turn: int,
    status_token: str,
) -> dict[str, Any] | None:
    """First turn after ``after_turn`` whose subject/body carries ``status_token``.

    Non-terminal statuses never qualify. Prefer subject-prefix matches but any
    occurrence in subject or body satisfies (F5 tag-based completion).
    """
    if status_token not in STATUS_COMPLETION_MODES:
        return None
    for t in sorted(turns, key=lambda r: r["turn_number"]):
        if t["turn_number"] <= after_turn:
            continue
        if _turn_carries_status_token(t, status_token):
            return t
    return None


DEAD_WAIT_ERROR = "dead_wait_no_auto_producer"
DEAD_WAIT_DETAIL = (
    "DISPOSITION one-correction does not enqueue cursor-auto. "
    "Re-issue amendment as agent_bus.request (TYPE: DIRECTIVE, density=sparse) "
    "and wait completion=status:done on the returned poll_hint."
)


def is_disposition_one_correction(turn: dict[str, Any] | None) -> bool:
    """True when turn body is TYPE: DISPOSITION with verdict one correction."""
    if turn is None:
        return False
    body = str(turn.get("body") or "").strip()
    if not body:
        return False
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if not lines or not first_line_is_disposition_type(lines[0]):
        return False
    for ln in lines[1:6]:
        low = ln.lower()
        if low.startswith("verdict:") and "one correction" in low:
            return True
    return False


def is_dead_wait_no_auto_producer(
    turns: list[dict[str, Any]],
    *,
    after_turn: int,
    completion: Completion,
) -> bool:
    """DISPOSITION one-correction + wait(from_agent=cursor) with no reply yet.

    Plain send does not enqueue lane:cursor-auto; that wait has no producer
    (operator-proxy dead-wait / friction 26253). cursor-auto and cursor-sdk
    waits are not this class.
    """
    if completion.get("mode", "first_reply_from") != "first_reply_from":
        return False
    from_agent = completion.get("from_agent")
    if not from_agent:
        return False
    if normalize_bus_address(from_agent) != "cursor":
        return False
    if qualifying_reply(turns, after_turn=after_turn, from_agent=from_agent):
        return False
    pointer = next((t for t in turns if t["turn_number"] == after_turn), None)
    return is_disposition_one_correction(pointer)


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
    expected = normalize_bus_address(from_agent) if from_agent is not None else None
    for t in sorted(turns, key=lambda r: r["turn_number"]):
        if t["turn_number"] <= after_turn:
            continue
        if expected is None or normalize_bus_address(t["from_agent"]) == expected:
            return t
    return None


def build_suggested_next(
    thread_row: dict[str, Any],
    *,
    complete: bool,
    completion: Completion,
    qualifying_reply_turn: int | None,
    after_turn: int,
    turns: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Advisory payload after ``first_reply_from`` — consult *bus turn* landed.

    ``complete`` here means a new turn from the consult seat exists (usually a
    short pointer to a sidecar), NOT that findings are applied or the handoff
    arc is finished. Wording avoids overloaded "reply" (turn 1 is the packet
    pointer from dispatch; turn 2+ is the consult deliverable).

    Lifecycle branches (F5):
    - Ephemeral or already-closed: no manual-close guidance (auto-close path).
    - Persistent + ``dispatch:close_on_read`` + unread result turn: mark-read only.
    - Persistent without close-on-read (or caller-owned): manual close advised.
    """
    if not complete or thread_row["status"] == ThreadStatus.CLOSED:
        return None
    if completion.get("mode", "first_reply_from") != "first_reply_from":
        return None
    if qualifying_reply_turn is None:
        return None

    tags = thread_row.get("tags") or []
    lifecycle = resolve_bus_lifecycle(tags)
    if lifecycle == "ephemeral":
        return None

    effective_turns = turns or []
    result_turn = next(
        (t for t in effective_turns if t["turn_number"] == qualifying_reply_turn),
        None,
    )
    result_unread = result_turn is not None and result_turn.get("read_at") is None

    base = {
        "phase": "consult_turn_posted",
        "pointer_turn": after_turn,
        "consult_turn": qualifying_reply_turn,
    }

    if CLOSE_ON_READ_TAG in tags and result_unread:
        return {
            **base,
            "message": (
                f"Consult posted agent-bus turn {qualifying_reply_turn} "
                f"(turn {after_turn} was the packet pointer only). "
                "Mark that turn read to auto-close this persistent dispatch thread."
            ),
            "steps": [
                {
                    "action": "mark_result_read",
                    "tool": "agent_bus",
                    "op": "mark_read",
                    "note": (
                        f"Mark turn {qualifying_reply_turn} read — "
                        "persistent dispatch closes on read."
                    ),
                },
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
            ],
        }

    if CLOSE_ON_READ_TAG in tags and not result_unread:
        return None

    return {
        **base,
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
    if mode in STATUS_COMPLETION_MODES:
        return (
            qualifying_status_turn(
                turns, after_turn=after_turn, status_token=str(mode)
            )
            is not None
        )
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
