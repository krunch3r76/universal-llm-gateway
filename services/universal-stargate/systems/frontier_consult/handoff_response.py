"""Handoff result-handle construction for POST /api/v1/team/handoff.

Builds the typed result_handle + handoff_status + poll_hint fragment appended
to the handoff response. Kept separate from route.py (thin) and handoff.py
(thread creation) per SRP.

The handle's ``kind`` is authoritative for source-of-truth routing: callers
dispatch retrieval on ``kind == "agent_bus_thread"`` to the bus, never to the
pipeline tracker. No pseudo execution_id is minted for handoff.
"""

from __future__ import annotations

import json
from typing import Any, Literal

# Ship C: only observable pre-reply state (no read_at-derived push proxy).
HandoffStatus = Literal["awaiting_first_reply"]

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

    ``arguments`` is a dict for human inspection; ``arguments_json`` is the
    MCP wire form (``agent_bus.arguments`` must be a JSON string).
    """
    wait_args = {
        "thread": thread_id,
        "after_turn": 1,
        "wait_seconds": 60,
        "completion": "first_reply_from",
        "from_agent": from_agent,
    }
    return {
        "tool": "wait",
        "arguments": wait_args,
        "arguments_json": json.dumps(wait_args, separators=(",", ":")),
    }


def build_push_reminder(*, thread_id: str, to_agent: str, platform: str) -> str:
    """Operator-facing reminder; web seats need a bus push, cursor seats need IDE attendance."""
    if platform == "cursor":
        return (
            f"**Action needed — attend agent-bus in Cursor**: handoff posted to thread "
            f"{thread_id}. Open the thread in Cursor (Multitask or /agent-bus) as "
            f"{to_agent}; switch to Opus in the model picker when this handoff needs it."
        )
    return (
        f"**Action needed — push to web claude**: handoff posted to thread "
        f"{thread_id}. Push the agent-bus message to trigger {to_agent}'s turn."
    )


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
