"""agent_bus ``hop`` — mechanical continuity-hop request-surface verb.

Authors a ``TYPE: CONTINUITY_HANDOFF`` body from ``hop_handoff``, then
delegates to ``_request_impl`` with ``continuity_hop=True`` so GIW admit
takes the existing hop path (no supersede, orientation prepend, CDP
commission). Not a contract token — handler short-circuits on the
structural flag before contract grading.
"""

from __future__ import annotations

from typing import Any

from hop_handoff import (
    assess_standing_handoff,
    build_continuity_handoff_body,
    parse_successor_birth_id,
)
from mcp_events import record

from .._agent_bus_author import resolve_dispatch_from_agent
from .request import _request_impl, _resolve_hop_seat_request_refusal
from .request_intake import resolve_request_id_intake

_VERB_SOURCE = "agent-bus-hop-verb"


def _hop_dispatch(
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
    ``reason`` becomes the body ``trigger:`` line. The verb reports
    *armed* (``handler_status``) — never ``status:done``. The successor
    selection key is ``successor_birth_id`` on the structural hop body
    (echoed onto the registration stamp). The MCP return is the
    predecessor's receipt and must not be read as the caller's own id.
    """
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
    )
    if seat_refusal is not None:
        return seat_refusal

    handoff = assess_standing_handoff(thread_id)
    body = build_continuity_handoff_body(
        thread_id=thread_id,
        trigger=trigger,
        source=_VERB_SOURCE,
        handoff=handoff,
        you_are=(cse_chat_url or "").strip() or None,
        superseded_registration_id=cse_registration_id,
    )
    hop_subject = (subject or "").strip() or (
        f"CONTINUITY HANDOFF — hop (thread {thread_id})"
    )
    result = _request_impl(
        new_slug=None,
        thread=thread_id,
        to="cursor",
        subject=hop_subject,
        body=body,
        from_agent=from_agent,
        tags=None,
        sidecar_content=None,
        sidecar_slug=None,
        desired_model=desired_model or "auto",
        desired_effort=desired_effort or "medium",
        contract="answer",
        require_attended=False,
        request_id=rid_intake.request_id,
        after_turn=after_turn,
        cse_chat_url=cse_chat_url,
        cse_registration_id=cse_registration_id,
        continuity_hop=True,
    )
    if isinstance(result, dict) and "error" in result:
        return result
    record(
        "mcp.agentbus.hop.posted",
        thread=thread_id,
        reason=trigger,
        handler_status=str(result.get("handler_status") or ""),
    )
    stamped = dict(result)
    stamped["continuity_hop"] = True
    birth_id = parse_successor_birth_id(body)
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
            "this MCP return is the predecessor's receipt — "
            "successor_birth_id names the successor, not the caller"
        ),
    }
    return stamped
