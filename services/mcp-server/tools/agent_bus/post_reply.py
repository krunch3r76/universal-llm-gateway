"""Deprecated post/reply agent_bus dispatchers (send-path predecessors)."""

from __future__ import annotations

import logging
from typing import Any

from mcp_events import record

from .._agent_bus_post_guard import structured_route_guard
from ._shared import (
    _format_agent_bus_error,
    _structured_body_too_large,
    _structured_relay_error,
    relay,
)

logger = logging.getLogger(__name__)


def _post_impl(
    *,
    slug: str,
    to: str,
    subject: str,
    body: str,
    from_agent: str,
    summary: str | None,
    attachments: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
    allow_long_body: bool = False,
) -> dict[str, Any]:
    """Atomic thread+turn creation via POST /threads/with-turn."""
    payload: dict[str, Any] = {
        "slug": slug,
        "from": from_agent,
        "to": to,
        "subject": subject,
        "body": body,
        "status": "open",
        "after_turn": 0,
    }
    if summary is not None:
        payload["summary"] = summary
    if attachments:
        payload["attachments"] = attachments
    if tags:
        payload["tags"] = tags
    if allow_long_body:
        payload["allow_long_body"] = True

    result = relay("agent-bus", "POST", "/threads/with-turn", body=payload)
    if "error" in result:
        record("mcp.agentbus.post.failed", slug=slug, to=to, error=str(result["error"]))
        structured = _structured_body_too_large(result, op="post")
        if structured is not None:
            return structured
        guard = structured_route_guard(result)
        if guard is not None:
            return guard
        structured = _structured_relay_error(result, op="post")
        if structured is not None:
            return structured
        return {"error": _format_agent_bus_error(result, op="post")}

    thread_data = result.get("thread", {})
    turn_data = result.get("turn", {})
    thread_id = thread_data.get("id", "")
    turn_number = turn_data.get("turn_number", 1)

    logger.info("agent_bus post: thread=%s slug=%s to=%s", thread_id, slug, to)
    record(
        "mcp.agentbus.thread.created",
        thread=thread_id,
        slug=slug,
        to=to,
        turn_number=turn_number,
    )
    if tags:
        record(
            "mcp.agentbus.thread.tags.updated",
            thread=thread_id,
            tag_count=len(tags),
            agent=from_agent,
            op="post",
        )
    return result


def _reply_impl(
    *,
    thread: str,
    to: str,
    subject: str,
    body: str,
    after_turn: int,
    from_agent: str,
    status: str,
    mark_read: bool,
    close: bool,
    attachments: list[dict[str, Any]] | None = None,
    allow_long_body: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "thread": thread,
        "from": from_agent,
        "to": to,
        "subject": subject,
        "body": body,
        "status": status,
    }
    if after_turn > 0:
        payload["after_turn"] = after_turn
    if attachments:
        payload["attachments"] = attachments
    if allow_long_body:
        payload["allow_long_body"] = True
    result = relay("agent-bus", "POST", "/turns", body=payload)

    if "error" in result:
        structured = _structured_body_too_large(result, op="reply")
        if structured is not None:
            return structured
        structured = _structured_relay_error(result, op="reply")
        if structured is not None:
            return structured
        return {"error": _format_agent_bus_error(result, op="reply")}

    turn_number = result.get("turn_number") or result.get("id")
    effective_turn_number = turn_number if turn_number is not None else 1
    logger.info(
        "agent_bus reply: thread=%s to=%s turn=%s", thread, to, effective_turn_number
    )
    record(
        "mcp.agentbus.turn.posted",
        thread=thread,
        to=to,
        turn_number=effective_turn_number,
    )

    if mark_read:
        turn_id = result.get("id")
        if turn_id is not None:
            relay("agent-bus", "PATCH", f"/turns/{turn_id}/read")
            logger.info("agent_bus reply: marked turn %s read (self-note)", turn_number)

    if close:
        close_result = relay("agent-bus", "PATCH", f"/threads/{thread}/close", body={})
        if isinstance(close_result, dict) and "error" in close_result:
            return {
                "error": (
                    f"reply posted but close failed: {close_result['error']}. "
                    f"Turn {effective_turn_number} exists; close manually."
                )
            }
        logger.info("agent_bus reply: closed thread %s after final turn", thread)
        record("mcp.agentbus.thread.closed", thread=thread, via="reply")
        result["closed"] = True

    return result


def _post_dispatch(
    *,
    slug: str = "",
    to: str = "",
    subject: str = "",
    body: str = "",
    from_agent: str = "",
    summary: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
    allow_long_body: bool = False,
) -> dict[str, Any]:
    missing: list[str] = []
    if not slug:
        missing.append("slug (str)")
    if not to:
        missing.append("to (str)")
    if not subject:
        missing.append("subject (str)")
    if not body:
        missing.append("body (str)")
    if not from_agent:
        missing.append(
            "from_agent (str, REQUIRED — no default; name the seat authoring "
            'this turn, e.g. "cursor", "claude-web", "gpt-cursor", "claude-api")'
        )
    if missing:
        return {
            "error": f"post: missing required field(s): {'; '.join(missing)}",
            "missing_fields": [f.split(" ")[0] for f in missing],
        }
    result = _post_impl(
        slug=slug,
        to=to,
        subject=subject,
        body=body,
        from_agent=from_agent,
        summary=summary,
        attachments=attachments,
        tags=tags,
        allow_long_body=allow_long_body,
    )
    if "error" not in result:
        result["_deprecated"] = {
            "op": "post",
            "remove_at": "2026-09-01",
            "replacement": "send",
        }
        record("mcp.agentbus.deprecated.op", op="post", caller=from_agent)
    return result


def _reply_dispatch(
    *,
    thread: str | int = "",
    to: str = "",
    subject: str = "",
    body: str = "",
    after_turn: int = 0,
    from_agent: str = "",
    status: str = "open",
    mark_read: bool = False,
    close: bool = False,
    attachments: list[dict[str, Any]] | None = None,
    allow_long_body: bool = False,
) -> dict[str, Any]:
    if isinstance(thread, int):
        thread = str(thread)

    missing: list[str] = []
    if not thread:
        missing.append('thread (str, e.g. "480")')
    if not to:
        missing.append("to (str)")
    if not subject:
        missing.append("subject (str)")
    if not body:
        missing.append("body (str)")
    if after_turn < 0:
        missing.append("after_turn (int >= 0; 0 skips the unread-concurrency check)")
    if not from_agent:
        missing.append(
            "from_agent (str, REQUIRED — no default; name the seat authoring "
            'this turn, e.g. "cursor", "claude-web", "gpt-cursor", "claude-api")'
        )
    if missing:
        return {
            "error": f"reply: missing required field(s): {'; '.join(missing)}",
            "missing_fields": [f.split(" ")[0] for f in missing],
        }
    result = _reply_impl(
        thread=thread,
        to=to,
        subject=subject,
        body=body,
        after_turn=after_turn,
        from_agent=from_agent,
        status=status,
        mark_read=mark_read,
        close=close,
        attachments=attachments,
        allow_long_body=allow_long_body,
    )
    if "error" not in result:
        result["_deprecated"] = {
            "op": "reply",
            "remove_at": "2026-09-01",
            "replacement": "send",
        }
        record("mcp.agentbus.deprecated.op", op="reply", caller=from_agent)
    return result
