"""Read-state and turn-edit dispatchers."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from mcp_events import record

from ._shared import _structured_relay_error, relay

logger = logging.getLogger(__name__)


def _resolve_turn_id(
    *, thread: str, turn_number: int
) -> tuple[int | None, dict[str, Any] | None]:
    """Resolve (thread, turn_number) to turn id via direct lookup."""
    qs = urlencode({"thread": thread, "turn_number": turn_number})
    result = relay("agent-bus", "GET", f"/turns/by-number?{qs}")
    if isinstance(result, dict) and "error" in result:
        return None, {"error": f"agent-bus error: {result['error']}"}
    if isinstance(result, dict) and "id" in result:
        return int(result["id"]), None
    return None, {"error": f"Turn {turn_number} not found in thread {thread}"}


def _update_impl(
    *,
    thread: str,
    turn_number: int,
    body: str | None,
    append: bool | str | None,
    subject: str | None,
) -> dict[str, Any]:
    import tools.agent_bus as pkg

    turn_id, resolve_error = pkg._resolve_turn_id(
        thread=thread, turn_number=turn_number
    )
    if resolve_error is not None:
        return resolve_error

    patch_body: dict[str, str | None] = {}
    if isinstance(append, str):
        patch_body["append"] = append
    elif append:
        if body is None:
            return {"error": "update with append=true requires body"}
        patch_body["append"] = body
    elif body is not None:
        patch_body["body"] = body
    if subject is not None:
        patch_body["subject"] = subject

    patch_result = relay("agent-bus", "PATCH", f"/turns/{turn_id}", body=patch_body)
    if isinstance(patch_result, dict) and "error" in patch_result:
        structured = _structured_relay_error(patch_result, op="update")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {patch_result['error']}"}

    logger.info(
        "agent_bus update: thread=%s turn=%d id=%d", thread, turn_number, turn_id
    )
    pkg.record(
        "mcp.agentbus.turn.updated",
        thread=thread,
        turn_number=turn_number,
        has_append=bool(append),
    )
    return patch_result


def _mark_read_dispatch(
    *,
    thread: str | int = "",
    turn_numbers: list[int] | None = None,
    through_turn: int | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """Bulk mark read — turn_numbers[] XOR through_turn (+ agent)."""
    if isinstance(thread, int):
        thread = str(thread)
    if not thread:
        return {"error": "mark_read requires: thread (str)"}
    has_list = turn_numbers is not None
    has_through = through_turn is not None
    if has_list == has_through:
        return {
            "error": (
                "mark_read: provide exactly one of turn_numbers (list[int]) "
                "or through_turn (int)"
            ),
            "reason": "read_state_xor_violation",
        }
    if has_through and not agent:
        return {
            "error": "mark_read: through_turn requires agent",
            "reason": "through_turn_requires_agent",
        }
    payload: dict[str, Any] = {}
    if turn_numbers is not None:
        payload["turn_numbers"] = turn_numbers
    else:
        payload["through_turn"] = through_turn
        payload["agent"] = agent
    result = relay(
        "agent-bus",
        "PATCH",
        f"/threads/{thread}/turns/read-state",
        body=payload,
    )
    if isinstance(result, dict) and "error" in result:
        structured = _structured_relay_error(result, op="mark_read")
        if structured is not None:
            return structured
        return result
    logger.info(
        "agent_bus mark_read: thread=%s marked_read=%s",
        thread,
        result.get("marked_read") if isinstance(result, dict) else "?",
    )
    record(
        "mcp.agentbus.turn.mark.read",
        thread=thread,
        marked_read=result.get("marked_read", 0) if isinstance(result, dict) else 0,
    )
    return {"status": "ok", "thread": thread, **(result if isinstance(result, dict) else {})}


def _update_dispatch(
    *,
    thread: str | int = "",
    turn_number: int = 0,
    body: str | None = None,
    append: bool | str | None = None,
    subject: str | None = None,
) -> dict[str, Any]:
    if isinstance(thread, int):
        thread = str(thread)
    if not thread or turn_number < 1:
        return {"error": "update requires: thread (str), turn_number (int >= 1)"}
    if body is None and append is None and not subject:
        return {"error": "update requires at least one of: body, append, subject"}
    if append is True and body is None:
        return {"error": "update with append=true requires body"}
    return _update_impl(
        thread=thread,
        turn_number=turn_number,
        body=body,
        append=append,
        subject=subject,
    )
