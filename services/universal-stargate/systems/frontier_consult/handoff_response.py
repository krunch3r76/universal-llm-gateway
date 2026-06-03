"""Handoff result-handle construction for POST /api/v1/team/handoff.

Builds the typed result_handle + handoff_status + poll_hint fragment appended
to the handoff response. Kept separate from route.py (thin) and handoff.py
(thread creation) per SRP.

The handle's ``kind`` is authoritative for source-of-truth routing: callers
dispatch retrieval on ``kind == "agent_bus_thread"`` to the bus, never to the
pipeline tracker. No pseudo execution_id is minted for handoff.
"""

from __future__ import annotations

from typing import Any, Literal

# Phase 1 default: only observable pre-reply state without a push proxy.
# Flip to "awaiting_push" iff the operator adopts the read_at pickup contract
# (claude-web mark_read on pointer turn) — see plan Open Q1 / Phase 4 gate.
HandoffStatus = Literal["awaiting_first_reply", "awaiting_push"]

_INITIAL_HANDOFF_STATUS: HandoffStatus = "awaiting_first_reply"


def build_result_handle(*, thread_id: str) -> dict[str, Any]:
    """Typed handle identifying the agent-bus thread as source of truth.

    ``after_turn`` is the pointer turn (1) the handoff just created; a reply
    is any turn with number > after_turn from the web seat.
    """
    return {
        "kind": "agent_bus_thread",
        "thread_id": thread_id,
        "after_turn": 1,
    }


def build_poll_hint_wait(*, thread_id: str, from_agent: str) -> dict[str, Any]:
    """Canonical poll_hint (Phase 2+): server-side wait args.

    fetch is now only a fallback; the wait op is the documented retrieval path.
    ``from_agent`` is the web seat whose first reply completes the handoff.
    """
    return {
        "tool": "wait",
        "arguments": {
            "thread": thread_id,
            "after_turn": 1,
            "wait_seconds": 60,
            "completion": "first_reply_from",
            "from_agent": from_agent,
        },
    }


def build_handoff_result(*, thread_id: str, to_agent: str) -> dict[str, Any]:
    """Assemble the three additive handoff-response fields.

    ``to_agent`` is the resolved web seat — it is the ``from_agent`` whose reply
    the wait hint waits on (the seat that will author the reply turn).
    """
    return {
        "result_handle": build_result_handle(thread_id=thread_id),
        "handoff_status": _INITIAL_HANDOFF_STATUS,
        "poll_hint": build_poll_hint_wait(thread_id=thread_id, from_agent=to_agent),
    }
