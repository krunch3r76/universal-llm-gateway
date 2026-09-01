"""Lifecycle dispatchers: close, update_thread, delete, wait."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from agent_bus_store.routes.wait import MAX_WAIT_SECONDS
from mcp_events import record

from .._agent_bus_author import resolve_dispatch_from_agent
from ._shared import _structured_relay_error, relay
from .read_state import _resolve_turn_id

logger = logging.getLogger(__name__)


def _add_tags_impl(
    *,
    thread: str,
    tags: list[str],
    from_agent: str,
    enroll_charter_runner: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"add_tags": tags}
    if enroll_charter_runner:
        payload["enroll_charter_runner"] = True
    result = relay("agent-bus", "PATCH", f"/threads/{thread}", body=payload)
    if "error" in result:
        structured = _structured_relay_error(result, op="add_tags")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}
    logger.info("agent_bus add_tags: thread=%s tags=%s", thread, tags)
    record(
        "mcp.agentbus.thread.tags.updated",
        thread=thread,
        tag_count=len(tags),
        agent=from_agent,
        op="add_tags",
    )
    return result


def _remove_tags_impl(
    *,
    thread: str,
    tags: list[str],
    from_agent: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"remove_tags": tags}
    result = relay("agent-bus", "PATCH", f"/threads/{thread}", body=payload)
    if "error" in result:
        structured = _structured_relay_error(result, op="remove_tags")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}
    logger.info("agent_bus remove_tags: thread=%s tags=%s", thread, tags)
    record(
        "mcp.agentbus.thread.tags.updated",
        thread=thread,
        tag_count=len(tags),
        agent=from_agent,
        op="remove_tags",
    )
    return result


def _close_impl(
    *,
    thread: str,
    summary: str | None,
    mark_all_read: bool,
) -> dict[str, Any]:
    """Atomic close via PATCH /threads/{id}/close."""
    payload: dict[str, Any] = {"mark_all_read": mark_all_read}
    if summary is not None:
        payload["summary"] = summary
    result = relay("agent-bus", "PATCH", f"/threads/{thread}/close", body=payload)
    if "error" in result:
        structured = _structured_relay_error(result, op="close")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}
    logger.info("agent_bus close: thread=%s", thread)
    # Store close_thread emits mcp.agentbus.thread.closed (SoT for CLI + HTTP).
    # Enrolled roots: close also strips charter-runner and emits
    # manage.charter.tick.root_closed (dispatch-board fold authority).
    return result


def _update_thread_impl(
    *,
    thread: str,
    status: str | None,
    summary: str | None,
    tags: list[str] | None,
    from_agent: str,
    enroll_charter_runner: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if status is not None:
        payload["status"] = status
    if summary is not None:
        payload["summary"] = summary
    if tags is not None:
        payload["tags"] = tags
    if enroll_charter_runner:
        payload["enroll_charter_runner"] = True
    if not payload:
        return {
            "error": "update_thread requires at least one of: status, summary, tags"
        }
    result = relay("agent-bus", "PATCH", f"/threads/{thread}", body=payload)
    if "error" in result:
        structured = _structured_relay_error(result, op="update_thread")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}
    logger.info("agent_bus update_thread: thread=%s status=%s", thread, status)
    record("mcp.agentbus.thread.updated", thread=thread, status=status or "")
    # status=closed → store update_thread also emits mcp.agentbus.thread.closed.
    # Closing an enrolled root (or stripping charter-runner from a closed root)
    # emits manage.charter.tick.root_closed for the dispatch-board fold.
    if tags is not None:
        record(
            "mcp.agentbus.thread.tags.updated",
            thread=thread,
            tag_count=len(tags),
            agent=from_agent,
            op="update_thread",
        )
    return result


def _delete_thread_impl(*, thread: str, force: bool) -> dict[str, Any]:
    params = {"force": "true"} if force else {}
    qs = urlencode(params)
    path = f"/threads/{thread}?{qs}" if qs else f"/threads/{thread}"
    result = relay("agent-bus", "DELETE", path)
    if isinstance(result, dict) and "error" in result:
        structured = _structured_relay_error(result, op="delete_thread")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}
    deleted_turns = result.get("deleted_turns", 0) if isinstance(result, dict) else 0
    logger.info(
        "agent_bus delete_thread: thread=%s force=%s deleted_turns=%d",
        thread,
        force,
        deleted_turns,
    )
    record(
        "mcp.agentbus.thread.deleted",
        thread=thread,
        force=force,
        deleted_turns=deleted_turns,
    )
    return result


def _delete_turn_impl(*, thread: str, turn_number: int, force: bool) -> dict[str, Any]:
    turn_id, resolve_error = _resolve_turn_id(thread=thread, turn_number=turn_number)
    if resolve_error is not None:
        return resolve_error
    force_params = urlencode({"force": "true"}) if force else ""
    path = f"/turns/{turn_id}?{force_params}" if force_params else f"/turns/{turn_id}"
    delete_result = relay("agent-bus", "DELETE", path)
    if isinstance(delete_result, dict) and "error" in delete_result:
        structured = _structured_relay_error(delete_result, op="delete_turn")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {delete_result['error']}"}
    logger.info(
        "agent_bus delete_turn: thread=%s turn=%d id=%d force=%s",
        thread,
        turn_number,
        turn_id,
        force,
    )
    record(
        "mcp.agentbus.turn.deleted", thread=thread, turn_number=turn_number, force=force
    )
    return delete_result


def _add_tags_dispatch(
    *,
    thread: str | int = "",
    tags: list[str] | None = None,
    from_agent: str = "",
    enroll_charter_runner: bool = False,
) -> dict[str, Any]:
    if isinstance(thread, int):
        thread = str(thread)
    if not thread:
        return {"error": "add_tags requires: thread (str)"}
    if not tags:
        return {"error": "add_tags requires: tags (list[str])"}
    from_agent, author_err = resolve_dispatch_from_agent(from_agent)
    if author_err is not None:
        return author_err
    return _add_tags_impl(
        thread=thread,
        tags=tags,
        from_agent=from_agent,
        enroll_charter_runner=enroll_charter_runner,
    )


def _remove_tags_dispatch(
    *,
    thread: str | int = "",
    tags: list[str] | None = None,
    from_agent: str = "",
) -> dict[str, Any]:
    if isinstance(thread, int):
        thread = str(thread)
    if not thread:
        return {"error": "remove_tags requires: thread (str)"}
    if not tags:
        return {"error": "remove_tags requires: tags (list[str])"}
    from_agent, author_err = resolve_dispatch_from_agent(from_agent)
    if author_err is not None:
        return author_err
    return _remove_tags_impl(
        thread=thread,
        tags=tags,
        from_agent=from_agent,
    )


def _close_dispatch(
    *,
    thread: str | int = "",
    summary: str | None = None,
    mark_all_read: bool = True,
) -> dict[str, Any]:
    if isinstance(thread, int):
        thread = str(thread)
    if not thread:
        return {"error": "close requires: thread (str)"}
    return _close_impl(
        thread=thread,
        summary=summary,
        mark_all_read=mark_all_read,
    )


def _update_thread_dispatch(
    *,
    thread: str | int = "",
    status: str | None = None,
    summary: str | None = None,
    tags: list[str] | None = None,
    from_agent: str = "",
    enroll_charter_runner: bool = False,
) -> dict[str, Any]:
    if isinstance(thread, int):
        thread = str(thread)
    if not thread:
        return {"error": "update_thread requires: thread (str)"}
    from_agent, author_err = resolve_dispatch_from_agent(from_agent)
    if author_err is not None:
        return author_err
    effective_status = status if (status and status != "open") else None
    return _update_thread_impl(
        thread=thread,
        status=effective_status,
        summary=summary,
        tags=tags,
        from_agent=from_agent,
        enroll_charter_runner=enroll_charter_runner,
    )


def _delete_thread_dispatch(
    *, thread: str | int = "", force: bool = False
) -> dict[str, Any]:
    if isinstance(thread, int):
        thread = str(thread)
    if not thread:
        return {"error": "delete_thread requires: thread (str)"}
    return _delete_thread_impl(thread=thread, force=force)


def _delete_turn_dispatch(
    *, thread: str | int = "", turn_number: int = 0, force: bool = False
) -> dict[str, Any]:
    if isinstance(thread, int):
        thread = str(thread)
    if not thread or turn_number < 1:
        return {"error": "delete_turn requires: thread (str), turn_number (int >= 1)"}
    return _delete_turn_impl(thread=thread, turn_number=turn_number, force=force)


def _wait_dispatch(
    *,
    thread: str | int = "",
    after_turn: int = 1,
    wait_seconds: float = 0.0,
    completion: str = "first_reply_from",
    from_agent: str | None = None,
) -> dict[str, Any]:
    """Thin relay to agent-bus GET /threads/{id}/wait."""
    if isinstance(thread, int):
        thread = str(thread)
    if not thread:
        return {"error": 'wait requires: thread (str, e.g. "1234")'}
    if completion == "first_reply_from" and not from_agent:
        return {"error": "wait with completion=first_reply_from requires from_agent"}
    from agent_bus_store.wait_status import STATUS_COMPLETION_MODES

    allowed = {"first_reply_from", "thread_closed", *STATUS_COMPLETION_MODES}
    if completion not in allowed:
        return {
            "error": (
                f"wait: unknown completion mode {completion!r}; "
                "expected first_reply_from | thread_closed | "
                "status:done | status:failed | status:needs-attended | "
                "status:superseded"
            ),
            "reason": "unknown_completion_mode",
        }
    wait_clamped = max(0.0, min(wait_seconds, MAX_WAIT_SECONDS))
    params: list[tuple[str, str]] = [
        ("after_turn", str(after_turn)),
        ("wait", str(wait_clamped)),
        ("completion", completion),
    ]
    if from_agent:
        params.append(("from_agent", from_agent))
    qs = urlencode(params)
    import tools.agent_bus as pkg

    wait_called_payload: dict[str, Any] = {
        "thread": thread,
        "completion": completion,
        "wait_seconds": wait_clamped,
    }
    if from_agent:
        wait_called_payload["from_agent"] = from_agent
    pkg.record("mcp.agentbus.wait.called", **wait_called_payload)
    terminal_status = "error"
    try:
        result = relay("agent-bus", "GET", f"/threads/{thread}/wait?{qs}")
        if isinstance(result, dict) and "error" in result:
            terminal_status = "relay_error"
            structured = _structured_relay_error(result, op="wait")
            if structured is not None:
                return structured
            return {"error": f"agent-bus error: {result['error']}"}
        terminal_status = (
            str(result.get("status", "")) if isinstance(result, dict) else ""
        )
        return result
    finally:
        pkg.record(
            "mcp.agentbus.wait.completed",
            thread=thread,
            status=terminal_status,
        )


def _triage_impl(
    *,
    from_agent: str,
    older_than: str,
    status: str | None,
    action: str,
    dry_run: bool,
    confirm_token: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "from": from_agent,
        "older_than": older_than,
        "action": action,
        "dry_run": dry_run,
    }
    if status is not None:
        payload["status"] = status
    if confirm_token is not None:
        payload["confirm_token"] = confirm_token
    result = relay("agent-bus", "POST", "/threads/triage", body=payload)
    if isinstance(result, dict) and "error" in result:
        structured = _structured_relay_error(result, op="triage")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}
    if dry_run:
        record(
            "mcp.agentbus.triage.preview",
            agent=from_agent,
            action=action,
            total_candidates=result.get("total_candidates", 0) if isinstance(result, dict) else 0,
        )
    else:
        record(
            "mcp.agentbus.triage.execute",
            agent=from_agent,
            action=action,
            thread_count=result.get("thread_count", 0) if isinstance(result, dict) else 0,
        )
    return result


def _triage_dispatch(
    *,
    from_agent: str = "",
    older_than: str = "",
    status: str | None = None,
    action: str = "mark_read",
    dry_run: bool = True,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    from_agent, author_err = resolve_dispatch_from_agent(from_agent)
    if author_err is not None:
        return author_err
    if not older_than:
        return {"error": "triage requires: older_than (str, e.g. '7d' or ISO8601)"}
    if action not in ("mark_read", "close"):
        return {
            "error": "triage: action must be mark_read or close",
            "reason": "invalid_triage_action",
        }
    return _triage_impl(
        from_agent=from_agent,
        older_than=older_than,
        status=status,
        action=action,
        dry_run=dry_run,
        confirm_token=confirm_token,
    )
