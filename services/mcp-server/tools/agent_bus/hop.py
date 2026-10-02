"""agent_bus ``hop`` — continuity hop via ``team_dispatch`` generate.

Authors a ``TYPE: CONTINUITY_HANDOFF`` body from ``hop_handoff``, then
relays ``op=generate`` through ``frontier._relay`` for a ``cdp/…`` successor.
Not a contract token.
"""

from __future__ import annotations

import re
from typing import Any

from hop_handoff import (
    assess_standing_handoff,
    build_continuity_handoff_body,
    parse_successor_birth_id,
)
from mcp_events import record

from .._agent_bus_author import resolve_dispatch_from_agent
from .request import _resolve_hop_seat_request_refusal
from .request_intake import resolve_request_id_intake

_VERB_SOURCE = "agent-bus-hop-verb"
_DEFAULT_SUCCESSOR_MODEL = "cdp/opus-5.5-high"
_HOP_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+/-]*$")


def resolve_hop_successor_model(desired_model: str) -> str:
    """Choose the CDP (or other prefixed) model a hop dispatches.

    Empty and ``auto`` stay on ``cdp/opus-5.5-high``. A slash-prefixed id is
    sent as given, so ``cdp/opus-5.5-max`` and ``cursor/grok-4.7`` pass through.
    A bare family such as ``fable-5.1-high`` is prefixed ``cdp/`` because the
    picker wire is ``cdp/<family>``.
    """
    raw = (desired_model or "").strip()
    if not raw or raw.lower() == "auto":
        return _DEFAULT_SUCCESSOR_MODEL
    model = raw if "/" in raw else f"cdp/{raw}"
    if _HOP_MODEL_ID.fullmatch(model) is None:
        raise ValueError(model)
    return model


async def _hop_dispatch(
    *,
    thread: str | int | None = None,
    reason: str = "",
    from_agent: str = "",
    cse_chat_url: str | None = None,
    cse_registration_id: str | None = None,
    desired_model: str = "",
    desired_effort: str = "",
    request_id: str | None = None,
    after_turn: int = 0,
    subject: str | None = None,
) -> dict[str, Any]:
    """Validate + dispatch ``agent_bus.hop``.

    ``thread`` is required (a hop is always on an existing private lane).
    ``reason`` becomes the body ``trigger:`` line. The verb reports the
    successor ``execution_id`` — never ``status:done``. The successor
    selection key is ``successor_birth_id`` on the structural hop body
    (echoed onto the registration stamp). When the seated CSE calls
    ``agent_bus.hop`` via MCP, the return is the predecessor's receipt and
    must not be read as the caller's own id.
    """
    del after_turn  # not a dispatch field
    if isinstance(thread, int):
        thread = str(thread)
    thread_id = (thread or "").strip()
    trigger = (reason or "").strip()
    if not thread_id:
        record("mcp.agentbus.hop.rejected", reason="thread_required")
        return {
            "error": "hop: thread is required (existing lane)",
            "reason": "hop_thread_required",
        }
    if not trigger:
        record("mcp.agentbus.hop.rejected", reason="reason_required")
        return {
            "error": "hop: reason is required",
            "reason": "hop_reason_required",
        }

    from_agent, author_err = resolve_dispatch_from_agent(from_agent)
    if author_err is not None:
        return author_err

    rid_intake = resolve_request_id_intake(
        request_id,
        thread_id=thread_id,
        contract="answer",
        from_agent=from_agent,
    )
    if rid_intake.error is not None:
        return rid_intake.error

    seat_refusal = _resolve_hop_seat_request_refusal(
        thread_id=thread_id,
        cse_registration_id=cse_registration_id,
        from_agent=from_agent,
    )
    if seat_refusal is not None:
        return seat_refusal

    try:
        model = resolve_hop_successor_model(desired_model)
    except ValueError:
        record("mcp.agentbus.hop.rejected", reason="model_invalid", thread=thread_id)
        return {
            "error": "hop: desired_model is not a model id",
            "reason": "hop_model_invalid",
        }

    handoff = assess_standing_handoff(thread_id)
    full_body = build_continuity_handoff_body(
        thread_id=thread_id,
        trigger=trigger,
        source=_VERB_SOURCE,
        handoff=handoff,
        occupy_target=(cse_chat_url or "").strip() or None,
        superseded_registration_id=cse_registration_id,
    )
    # mission_kind=hop arms Stargate hop_own_generate so the caller's own
    # live generate is excluded (a:37182). predecessor_registration_id names
    # that generate when the seated CSE supplies cse_registration_id; without
    # it, Stargate still exempts the sole live gate on the lane.
    body: dict[str, Any] = {
        "op": "generate",
        "model": model,
        "prompt": full_body,
        "job": "freeform",
        "session": "operator-proxy",
        "mission_kind": "hop",
        "parent_thread": thread_id,
        "dispatch_thread_id": thread_id,
        "caller_agent": from_agent,
    }
    reg = (cse_registration_id or "").strip()
    if reg:
        body["predecessor_registration_id"] = reg
    effort = (desired_effort or "").strip()
    if effort and effort != "auto":
        body["reasoning_effort"] = effort

    from tools.frontier import _relay

    result = await _relay(
        endpoint="/api/v1/team/dispatch",
        body=body,
        record_prefix="mcp.agentbus.hop.dispatch",
    )
    if isinstance(result, dict) and "error" in result:
        return result
    execution_id = str(result.get("execution_id") or "") if isinstance(result, dict) else ""
    record(
        "mcp.agentbus.hop.posted",
        thread=thread_id,
        reason=trigger,
        execution_id=execution_id,
    )
    stamped = dict(result) if isinstance(result, dict) else {"result": result}
    stamped["continuity_hop"] = True
    stamped["execution_id"] = execution_id
    stamped["model"] = model
    birth_id = parse_successor_birth_id(full_body)
    stamped["successor"] = {
        "handle": "successor_birth_id",
        "names": "successor",
        "value": birth_id,
        "where": (
            "structural TYPE: CONTINUITY_HANDOFF body on this lane "
            "(successor first-turn tokens); echoed onto TYPE: "
            "SEAT_REGISTRATION stamp at registration observation"
        ),
        "note": (
            "when this seated CSE calls agent_bus.hop via MCP, this return "
            "is the predecessor's receipt — successor_birth_id names the "
            "successor, not the caller"
        ),
    }
    return stamped
