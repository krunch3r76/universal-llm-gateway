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
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from agent_seat.profiles import CapabilityProfile

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


def build_recommended_executor(
    *,
    handoff_contract: str,
    packet_text: str,
    executor_override: str | None = None,
    executor_override_reason_code: str | None = None,
    executor_override_reason: str | None = None,
) -> dict[str, Any]:
    """Assemble executor advisory fields for the handoff response."""
    from .executor_resolution import derive_recommended_executor

    return derive_recommended_executor(
        handoff_contract,
        packet_text,
        executor_override=executor_override,
        executor_override_reason_code=executor_override_reason_code,
        executor_override_reason=executor_override_reason,
    )


def build_recommended_review(*, handoff_contract: str) -> dict[str, Any]:
    """Consult adversarial-pass advisory (Q-sibling dual)."""
    from .executor_resolution import derive_recommended_review

    value = derive_recommended_review(handoff_contract)
    return {"recommended_review": value}


def build_seat_capability(
    *,
    profile: CapabilityProfile,
    recommended_executor: str | None,
) -> dict[str, Any]:
    """Seat-capability advisory block for the handoff response."""
    return {
        "seat_capability": {
            "delivery": profile.delivery,
            "api_dispatchable": profile.api_dispatchable,
            "auto_dispatchable": profile.auto_dispatchable,
            "manual_handoff": profile.manual_handoff,
            "tool_surface": profile.tool_surface,
            "picker_range": list(profile.allowed_models),
            "default_model": profile.default_model,
            "recommended_executor": recommended_executor,
        }
    }


def build_push_reminder(
    *,
    thread_id: str,
    to_agent: str,
    platform: str,
    handoff_contract: str | None = None,
) -> str:
    """Operator-facing reminder.

    Web seats need a bus push; cursor seats need IDE attendance.
    """
    seat_obligation = (
        " Dispatching seat: translate this for the operator in your reply — "
        "what was dispatched and to whom, what runs autonomously, exactly what "
        "the operator must do, and how results return. Do not paste this "
        "reminder verbatim."
    )
    if platform == "sdk":
        return (
            f"**Automated cursor-sdk dispatch**: handoff posted to thread "
            f"{thread_id}. Worker admission runs asynchronously; poll via "
            f"poll_hint for the first reply from cursor-sdk."
            f"{seat_obligation}"
        )
    if platform == "cursor":
        executor_hint = (
            " Default executor for bound implement handoffs is Composer "
            "(Thinking ≡ Fast); pick Composer in the model picker unless the "
            "server advisory names a structured override."
            if handoff_contract == "implement"
            else ""
        )
        return (
            f"**Action needed — attend agent-bus in Cursor**: handoff posted to thread "
            f"{thread_id}. Open the thread in Cursor (Multitask or /agent-bus) as "
            f"{to_agent}.{executor_hint}"
            f"{seat_obligation}"
        )
    return (
        f"**Action needed — push to web claude**: handoff posted to thread "
        f"{thread_id}. Push the agent-bus message to trigger {to_agent}'s turn."
        f"{seat_obligation}"
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


def build_sdk_generate_result(
    *,
    role: str,
    profile: CapabilityProfile,
    handoff_fields: dict[str, Any],
    execution_id: str,
    thread_id: str,
    to_agent: str,
    resolved_model: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Generate-shaped 202 response for SDK-substrate cursor-sdk dispatch."""
    result: dict[str, Any] = {
        **handoff_fields,
        "execution_id": execution_id,
        "substrate": "sdk",
        "output_contract": "thread",
        "thread_id": thread_id,
        "to_agent": to_agent,
        "resolved_model": resolved_model,
        "capabilities": {
            "role": role,
            "inline_only": True,
            "mcp_enabled": False,
            "tool_surface": profile.tool_surface,
            "resolved_model": resolved_model,
            "substrate": "sdk",
        },
        "poll_hint": handoff_fields["poll_hint"],
        "result_handle": {
            "kind": "dual",
            "execution_id": execution_id,
            "thread_id": thread_id,
            "substrate": "sdk",
            "durable": False,
        },
    }
    if warnings:
        result["warnings"] = warnings
    return result
