"""Unified send agent_bus dispatcher."""

from __future__ import annotations

from typing import Any

from mcp_events import record

from .._agent_bus_author import resolve_dispatch_from_agent
from .._agent_bus_post_guard import structured_slug_exists
from ._shared import (
    _format_agent_bus_error,
    _structured_body_too_large,
    _structured_relay_error,
    _structured_sidecar_write_failed,
    relay,
)


def _send_impl(
    *,
    new_slug: str | None,
    thread: str | None,
    to: str,
    subject: str,
    body: str,
    from_agent: str,
    summary: str | None,
    tags: list[str] | None,
    lifecycle_state: str | None,
    after_turn: int,
    status: str,
    mark_read: bool,
    close: bool,
    attachments: list[dict[str, Any]] | None,
    allow_long_body: bool,
    sidecar_content: str | None = None,
    sidecar_slug: str | None = None,
    supersedes_turn: int | None = None,
    supersedes_turn_id: int | None = None,
    enroll_charter_runner: bool = False,
    parent_thread: str | None = None,
    lane_role: str | None = None,
) -> dict[str, Any]:
    """Relay to POST /threads/send."""
    payload: dict[str, Any] = {
        "from": from_agent,
        "to": to,
        "subject": subject,
        "body": body,
        "status": status,
    }
    if new_slug is not None:
        payload["new_slug"] = new_slug
    if thread is not None:
        payload["thread"] = thread
    if summary is not None:
        payload["summary"] = summary
    if tags:
        payload["tags"] = tags
    if enroll_charter_runner:
        payload["enroll_charter_runner"] = True
    if lifecycle_state is not None:
        payload["lifecycle_state"] = lifecycle_state
    if after_turn > 0:
        payload["after_turn"] = after_turn
    if mark_read:
        payload["mark_read"] = True
    if close:
        payload["close"] = True
    if attachments:
        payload["attachments"] = attachments
    if allow_long_body:
        payload["allow_long_body"] = True
    if sidecar_content is not None:
        payload["sidecar_content"] = sidecar_content
    if sidecar_slug is not None:
        payload["sidecar_slug"] = sidecar_slug
    if supersedes_turn is not None:
        payload["supersedes_turn"] = supersedes_turn
    elif supersedes_turn_id is not None:
        payload["supersedes_turn_id"] = supersedes_turn_id
    if parent_thread is not None:
        payload["parent_thread"] = parent_thread
    if lane_role is not None:
        payload["lane_role"] = lane_role

    result = relay("agent-bus", "POST", "/threads/send", body=payload)
    if "error" in result:
        record("mcp.agentbus.send.failed", error=str(result["error"]))
        structured = _structured_body_too_large(result, op="send")
        if structured is not None:
            return structured
        structured = _structured_sidecar_write_failed(result)
        if structured is not None:
            return structured
        structured = structured_slug_exists(result)
        if structured is not None:
            return structured
        structured = _structured_relay_error(result, op="send")
        if structured is not None:
            return structured
        return {"error": _format_agent_bus_error(result, op="send")}

    send_path = result.get("send_path", "")
    thread_id = (result.get("thread") or {}).get("id", "")
    turn_number = (result.get("turn") or {}).get("turn_number", 1)
    record(
        "mcp.agentbus.send.posted",
        send_path=send_path,
        thread=thread_id,
        to=to,
        turn_number=turn_number,
    )
    from claude_bundles.mission_close_debrief_auto import attach_mission_debrief_notify

    return attach_mission_debrief_notify(
        result,
        subject=subject,
        body=body,
        thread_id=thread_id,
        from_agent=from_agent,
    )


def _send_dispatch(
    *,
    new_slug: str | None = None,
    thread: str | int | None = None,
    to: str = "",
    subject: str = "",
    body: str = "",
    from_agent: str = "",
    summary: str | None = None,
    tags: list[str] | None = None,
    lifecycle_state: str | None = None,
    after_turn: int = 0,
    status: str = "open",
    mark_read: bool = False,
    close: bool = False,
    attachments: list[dict[str, Any]] | None = None,
    allow_long_body: bool = False,
    sidecar_content: str | None = None,
    sidecar_slug: str | None = None,
    supersedes_turn: int | None = None,
    supersedes_turn_id: int | None = None,
    enroll_charter_runner: bool = False,
    parent_thread: str | None = None,
    lane_role: str | None = None,
) -> dict[str, Any]:
    if isinstance(thread, int):
        thread = str(thread)

    from_agent, author_err = resolve_dispatch_from_agent(from_agent)
    if author_err is not None:
        return author_err

    has_new_slug = new_slug is not None
    has_thread = bool(thread)
    if has_new_slug and has_thread:
        record("mcp.agentbus.send.rejected", reason="xor_both")
        return {
            "error": (
                "send: thread and new_slug are mutually exclusive — "
                "provide exactly one to route the turn"
            ),
            "reason": "send_xor_violation",
            "provided": ["thread", "new_slug"],
            "required": "exactly_one_of_thread_or_new_slug",
        }
    if not has_new_slug and not has_thread:
        record("mcp.agentbus.send.rejected", reason="xor_neither")
        return {
            "error": (
                "send: exactly one of thread or new_slug is required — "
                "neither was provided"
            ),
            "reason": "send_xor_violation",
            "provided": [],
            "required": "exactly_one_of_thread_or_new_slug",
        }

    missing: list[str] = []
    if not to:
        missing.append("to (str)")
    if not subject:
        missing.append("subject (str)")
    if not body:
        missing.append("body (str)")
    if not from_agent:
        missing.append(
            "from (str) or from_agent (str) — omit only on /mcp/life or /mcp/code "
            "for surface autofill (web-anthropic | cursor)"
        )
    if missing:
        return {
            "error": f"send: missing required field(s): {'; '.join(missing)}",
            "missing_fields": [f.split(" ")[0] for f in missing],
        }

    if has_new_slug and after_turn > 0:
        return {
            "error": (
                "send: after_turn > 0 is invalid on the new_slug (new-thread) path"
            ),
            "reason": "after_turn_not_valid_on_new_thread",
            "suggestion": "omit after_turn on new-thread path (it has no meaning)",
        }
    if has_thread and lifecycle_state is not None:
        return {
            "error": (
                "send: lifecycle_state is only valid on the new_slug (new-thread) path"
            ),
            "reason": "lifecycle_state_not_valid_on_continue",
        }

    if has_new_slug and (supersedes_turn is not None or supersedes_turn_id is not None):
        return {
            "error": (
                "send: supersedes_turn is only valid on the continue (thread=) path"
            ),
            "reason": "supersedes_turn_not_valid_on_new_thread",
        }

    from claude_bundles.lane_a_closeout_checkpoint import (
        refusal_envelope as checkpoint_refusal_envelope,
    )
    from claude_bundles.lane_a_closeout_checkpoint import (
        validate_lane_a_closeout_checkpoint,
    )
    from claude_bundles.mission_close_wake import (
        refusal_envelope,
        validate_mission_close_wake,
    )

    wake = validate_mission_close_wake(subject=subject, body=body)
    if not wake.ok:
        record(
            "mcp.agentbus.send.rejected",
            reason=wake.reason or "mission_close_wake_path_missing",
        )
        return refusal_envelope(wake)

    from .pickup_gate import refuse_if_pickup_awaits

    pickup_refusal = refuse_if_pickup_awaits(
        subject=subject,
        body=body,
        thread=thread if has_thread else None,
        event_prefix="mcp.agentbus.send",
    )
    if pickup_refusal is not None:
        return pickup_refusal

    checkpoint = validate_lane_a_closeout_checkpoint(subject=subject, body=body)
    if not checkpoint.ok:
        record(
            "mcp.agentbus.send.rejected",
            reason=checkpoint.reason or "lane_a_checkpoint_missing",
        )
        return checkpoint_refusal_envelope(checkpoint)

    return _send_impl(
        new_slug=new_slug,
        thread=thread,
        to=to,
        subject=subject,
        body=body,
        from_agent=from_agent,
        summary=summary,
        tags=tags,
        lifecycle_state=lifecycle_state,
        after_turn=after_turn,
        status=status,
        mark_read=mark_read,
        close=close,
        attachments=attachments,
        allow_long_body=allow_long_body,
        sidecar_content=sidecar_content,
        sidecar_slug=sidecar_slug,
        supersedes_turn=supersedes_turn,
        supersedes_turn_id=supersedes_turn_id,
        enroll_charter_runner=enroll_charter_runner,
        parent_thread=parent_thread,
        lane_role=lane_role,
    )
