"""Agent-bus tools — dispatch-style MCP interface to the Agent Bus service.

Exposes a single ``agent_bus(tool=..., arguments=...)`` tool that routes to
the Agent Bus REST API over UDS. Uses the same dispatch calling convention
as the primary dispatch() tool.

All HTTP I/O delegates to ``_relay()`` from ``local_api.py``.
"""

from __future__ import annotations

import inspect
import logging
import os
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from mcp_events import record
from mcp_toolprogress import toolprogress_begin, toolprogress_end

from ._local_relay import relay as _relay

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP

logger = logging.getLogger(__name__)
_PREVIEW_MAX_LAST = max(1, int(os.getenv("MCP_AGENT_BUS_PREVIEW_MAX_LAST", "20")))


def _structured_body_too_large(
    result: dict[str, Any], *, op: str
) -> dict[str, Any] | None:
    """Re-shape a relay 413 detail into the legacy structured error envelope.

    REST returns ``{"detail": {"reason": "body_too_large", "limit_chars": ...,
    "body_chars": ..., "suggestion": ..., "message": ...}}`` on 413; the relay
    surfaces ``detail`` alongside ``error``. Agents previously got
    ``{error, reason, limit_chars, body_chars, suggestion}`` from the MCP
    preflight — preserve that shape so existing callers keep their
    discriminator fields.
    """
    detail = result.get("detail")
    if not (isinstance(detail, dict) and detail.get("reason") == "body_too_large"):
        return None
    limit = detail.get("limit_chars")
    body_chars = detail.get("body_chars")
    return {
        "error": (
            f"{op}: turn body exceeds limit "
            f"({body_chars:,} chars, limit {limit:,}). "
            "Agent-bus convention: short briefing + sidecar markdown reference. "
            "Write long content to notes/system/threads/<thread>-<subject>.md "
            "and reference it in a brief body."
        ),
        "reason": "body_too_large",
        "limit_chars": limit,
        "body_chars": body_chars,
        "suggestion": detail.get("suggestion", "sidecar_markdown_or_trim"),
    }


# ── Impl helpers ────────────────────────────────────────────────────


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

    result = _relay("agent-bus", "POST", "/threads/with-turn", body=payload)
    if "error" in result:
        record("mcp.agentbus.post.failed", slug=slug, to=to, error=str(result["error"]))
        structured = _structured_body_too_large(result, op="post")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error creating thread: {result['error']}"}

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
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "thread": thread,
        "from": from_agent,
        "to": to,
        "subject": subject,
        "body": body,
        "status": status,
        "after_turn": after_turn,
    }
    if attachments:
        payload["attachments"] = attachments
    result = _relay("agent-bus", "POST", "/turns", body=payload)

    if "error" in result:
        structured = _structured_body_too_large(result, op="reply")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}

    turn_number = result.get("turn_number") or result.get("id")
    # If turn_number is still None, it indicates a problem, consider raising or logging an error more prominently.
    # For now, default to 1 if it's expected to be a positive integer.
    effective_turn_number = (
        turn_number if turn_number is not None else 1
    )  # Or handle as an error if 1 is not a safe default
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
            _relay("agent-bus", "PATCH", f"/turns/{turn_id}/read")
            logger.info("agent_bus reply: marked turn %s read (self-note)", turn_number)

    if close:
        close_result = _relay("agent-bus", "PATCH", f"/threads/{thread}/close", body={})
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


def _fetch_impl(
    *,
    to: str | None,
    thread: str | None,
    last: int,
    unread: bool,
    mark_read: bool,
    compact: bool,
) -> dict[str, Any]:
    if to is None and thread is None:
        return {"error": "fetch requires at least one of: to, thread"}

    params: dict[str, Any] = {}
    if thread is not None:
        params["thread"] = thread
    if to is not None:
        params["to"] = to
        params["unread"] = unread
        params["compact"] = compact
    params["last"] = last
    if mark_read:
        params["mark_read"] = "true"

    qs = urlencode(params)
    result = _relay("agent-bus", "GET", f"/turns?{qs}")

    if "error" in result:
        return {"error": f"agent-bus error: {result['error']}"}

    turns: list[Any] = result if isinstance(result, list) else result.get("turns", [])
    count = len(turns)
    logger.info(
        "agent_bus fetch: to=%s thread=%s mark_read=%s -> %d turns",
        to,
        thread,
        mark_read,
        count,
    )
    record(
        "mcp.agentbus.turns.fetched",
        to=to or "",
        thread=thread or "",
        count=count,
        mark_read=mark_read,
    )
    return result


def _get_impl(*, thread: str, turn_number: int) -> dict[str, Any]:
    """Direct single-turn lookup via GET /turns/by-number."""
    qs = urlencode({"thread": thread, "turn_number": turn_number})
    result = _relay("agent-bus", "GET", f"/turns/by-number?{qs}")
    if isinstance(result, dict) and "error" in result:
        return {"error": f"agent-bus error: {result['error']}"}
    record("mcp.agentbus.turn.detail.fetched", thread=thread, turn_number=turn_number)
    return {"turn": result}  # Return type should be dict[str, dict[str, Any]]


def _resolve_turn_id(
    *, thread: str, turn_number: int
) -> tuple[int | None, dict[str, Any] | None]:
    """Resolve (thread, turn_number) to turn id via direct lookup."""
    qs = urlencode({"thread": thread, "turn_number": turn_number})
    result = _relay("agent-bus", "GET", f"/turns/by-number?{qs}")
    if isinstance(result, dict) and "error" in result:
        return None, {"error": f"agent-bus error: {result['error']}"}
    if isinstance(result, dict) and "id" in result:
        # Consider adding a check if result["id"] is actually an int or can be safely cast.
        # For now, assuming it's safe based on API contract.
        return int(result["id"]), None
    return None, {"error": f"Turn {turn_number} not found in thread {thread}"}


def _threads_impl(
    *,
    status: str,
    tags: list[str] | None = None,
    lifecycle_state: str | None = None,
) -> dict[str, Any]:
    params: list[tuple[str, str]] = []
    if status != "all":
        params.append(("status", status))
    tag_list = [t.strip() for t in (tags or []) if t and t.strip()]
    for tag in tag_list:
        params.append(("tags", tag))
    if lifecycle_state:
        params.append(("lifecycle_state", lifecycle_state))
    qs = urlencode(params)
    path = f"/threads?{qs}" if qs else "/threads"
    result = _relay("agent-bus", "GET", path)

    if "error" in result:
        return {"error": f"agent-bus error: {result['error']}"}

    threads: list[Any] = (
        result if isinstance(result, list) else result.get("threads", [])
    )
    count = len(threads)
    logger.info(
        "agent_bus threads: status=%s lifecycle=%s tags=%s -> %d threads",
        status,
        lifecycle_state or "-",
        ",".join(tag_list) or "-",
        count,
    )
    record(
        "mcp.agentbus.threads.listed",
        status=status,
        tag_count=len(tag_list),
        count=count,
    )
    return result


def _create_thread_impl(
    *,
    slug: str,
    summary: str | None = None,
    tags: list[str] | None = None,
    lifecycle_state: str | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Create a thread without a turn via POST /threads."""
    payload: dict[str, Any] = {"slug": slug}
    if summary is not None:
        payload["summary"] = summary
    if tags:
        payload["tags"] = tags
    if lifecycle_state is not None:
        payload["lifecycle_state"] = lifecycle_state
    if thread_id is not None:
        payload["id"] = thread_id
    result = _relay("agent-bus", "POST", "/threads", body=payload)
    if isinstance(result, dict) and "error" in result:
        return {"error": f"agent-bus error creating thread: {result['error']}"}
    created_id = result.get("id", "") if isinstance(result, dict) else ""
    logger.info("agent_bus create_thread: thread=%s slug=%s", created_id, slug)
    record(
        "mcp.agentbus.thread.created",
        thread=created_id,
        slug=slug,
        via="create_thread",
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
    result = _relay("agent-bus", "PATCH", f"/threads/{thread}/close", body=payload)
    if "error" in result:
        return {"error": f"agent-bus error: {result['error']}"}
    logger.info("agent_bus close: thread=%s", thread)
    record("mcp.agentbus.thread.closed", thread=thread)
    return result


def _update_thread_impl(
    *,
    thread: str,
    status: str | None,
    summary: str | None,
    tags: list[str] | None,
    from_agent: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if status is not None:
        payload["status"] = status
    if summary is not None:
        payload["summary"] = summary
    if tags is not None:
        # [] = clear, [...] = replace. None = omit so server leaves unchanged.
        payload["tags"] = tags
    if not payload:
        return {
            "error": "update_thread requires at least one of: status, summary, tags"
        }
    result = _relay("agent-bus", "PATCH", f"/threads/{thread}", body=payload)
    if "error" in result:
        return {"error": f"agent-bus error: {result['error']}"}
    logger.info("agent_bus update_thread: thread=%s status=%s", thread, status)
    record("mcp.agentbus.thread.updated", thread=thread, status=status or "")
    if tags is not None:
        record(
            "mcp.agentbus.thread.tags.updated",
            thread=thread,
            tag_count=len(tags),
            agent=from_agent,
            op="update_thread",
        )
    return result


def _update_impl(
    *,
    thread: str,
    turn_number: int,
    body: str | None,
    append: bool | str | None,
    subject: str | None,
) -> dict[str, Any]:
    turn_id, resolve_error = _resolve_turn_id(thread=thread, turn_number=turn_number)
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

    patch_result = _relay("agent-bus", "PATCH", f"/turns/{turn_id}", body=patch_body)
    if isinstance(patch_result, dict) and "error" in patch_result:
        return {"error": f"agent-bus error: {patch_result['error']}"}

    logger.info(
        "agent_bus update: thread=%s turn=%d id=%d", thread, turn_number, turn_id
    )
    record(
        "mcp.agentbus.turn.updated",
        thread=thread,
        turn_number=turn_number,
        has_append=bool(append),
    )
    return patch_result


def _delete_thread_impl(*, thread: str, force: bool) -> dict[str, Any]:
    params = {"force": "true"} if force else {}
    qs = urlencode(params)
    path = f"/threads/{thread}?{qs}" if qs else f"/threads/{thread}"
    result = _relay("agent-bus", "DELETE", path)
    if isinstance(result, dict) and "error" in result:
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
    delete_result = _relay("agent-bus", "DELETE", path)
    if isinstance(delete_result, dict) and "error" in delete_result:
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


# ── Dispatch wrappers (validation + defaults for JSON dispatch) ─────────────


def _fetch_dispatch(
    *,
    to: str | None = None,
    thread: str | None = None,
    last: int = 5,
    unread: bool = True,
    mark_read: bool = False,
    compact: bool = True,
) -> dict[str, Any]:
    """Dispatch wrapper for fetch — applies preview cap and normalizes empty strings."""
    effective_to = to if to else None
    effective_thread = thread if thread else None
    safe_last = max(1, min(last, _PREVIEW_MAX_LAST))
    return _fetch_impl(
        to=effective_to,
        thread=effective_thread,
        last=safe_last,
        unread=unread,
        mark_read=mark_read,
        compact=compact,
    )


def _post_dispatch(
    *,
    slug: str = "",
    to: str = "",
    subject: str = "",
    body: str = "",
    from_agent: str = "cursor",
    summary: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    if not slug or not to or not subject or not body:
        return {"error": "post requires: slug, to, subject, body"}
    return _post_impl(
        slug=slug,
        to=to,
        subject=subject,
        body=body,
        from_agent=from_agent,
        summary=summary,
        attachments=attachments,
        tags=tags,
    )


def _reply_dispatch(
    *,
    thread: str = "",
    to: str = "",
    subject: str = "",
    body: str = "",
    after_turn: int = 0,
    from_agent: str = "cursor",
    status: str = "open",
    mark_read: bool = False,
    close: bool = False,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not thread or not to or not subject or not body or after_turn < 1:
        return {"error": "reply requires: thread, to, subject, body, after_turn"}
    return _reply_impl(
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
    )


def _get_dispatch(*, thread: str = "", turn_number: int = 0) -> dict[str, Any]:
    if not thread or turn_number < 1:
        return {"error": "get requires: thread, turn_number (>= 1)"}
    return _get_impl(thread=thread, turn_number=turn_number)


def _threads_dispatch(
    *,
    status: str = "active",
    tags: list[str] | None = None,
    lifecycle_state: str | None = None,
) -> dict[str, Any]:
    return _threads_impl(status=status, tags=tags, lifecycle_state=lifecycle_state)


def _create_thread_dispatch(
    *,
    slug: str = "",
    summary: str | None = None,
    tags: list[str] | None = None,
    lifecycle_state: str | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    if not slug:
        return {"error": "create_thread requires: slug"}
    return _create_thread_impl(
        slug=slug,
        summary=summary,
        tags=tags,
        lifecycle_state=lifecycle_state,
        thread_id=thread_id,
    )


def _update_dispatch(
    *,
    thread: str = "",
    turn_number: int = 0,
    body: str | None = None,
    append: bool | str | None = None,
    subject: str | None = None,
) -> dict[str, Any]:
    if not thread or turn_number < 1:
        return {"error": "update requires: thread, turn_number (>= 1)"}
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


def _update_thread_dispatch(
    *,
    thread: str = "",
    status: str | None = None,
    summary: str | None = None,
    tags: list[str] | None = None,
    from_agent: str = "cursor",
) -> dict[str, Any]:
    if not thread:
        return {"error": "update_thread requires: thread"}
    # If an empty string status is not allowed, add explicit validation here.
    # For now, assuming empty string should be treated as None for status updates.
    effective_status = status if (status and status != "open") else None
    return _update_thread_impl(
        thread=thread,
        status=effective_status,
        summary=summary,
        tags=tags,
        from_agent=from_agent,
    )


def _close_dispatch(
    *,
    thread: str = "",
    summary: str | None = None,
    mark_all_read: bool = True,
) -> dict[str, Any]:
    if not thread:
        return {"error": "close requires: thread"}
    return _close_impl(
        thread=thread,
        summary=summary,
        mark_all_read=mark_all_read,
    )


def _delete_thread_dispatch(*, thread: str = "", force: bool = False) -> dict[str, Any]:
    if not thread:
        return {"error": "delete_thread requires: thread"}
    return _delete_thread_impl(thread=thread, force=force)


def _delete_turn_dispatch(
    *, thread: str = "", turn_number: int = 0, force: bool = False
) -> dict[str, Any]:
    if not thread or turn_number < 1:
        return {"error": "delete_turn requires: thread, turn_number (>= 1)"}
    return _delete_turn_impl(thread=thread, turn_number=turn_number, force=force)


def _mark_read_dispatch(*, thread: str = "", turn_number: int = 0) -> dict[str, Any]:
    """Mark a specific turn as read. Clears it from unread counts."""
    if not thread or turn_number < 1:
        return {"error": "mark_read requires: thread, turn_number (>= 1)"}
    turn_id, err = _resolve_turn_id(thread=thread, turn_number=turn_number)
    if err:
        return err
    result = _relay("agent-bus", "PATCH", f"/turns/{turn_id}/read")
    if isinstance(result, dict) and "error" in result:
        return result
    logger.info("agent_bus mark_read: thread=%s turn=%d", thread, turn_number)
    record("mcp.agentbus.turn.marked_read", thread=thread, turn_number=turn_number)
    return {"status": "ok", "thread": thread, "turn_number": turn_number}


_AGENT_BUS_OPS: dict[str, Callable[..., Any]] = {
    "post": _post_dispatch,
    "reply": _reply_dispatch,
    "fetch": _fetch_dispatch,
    "get": _get_dispatch,
    "threads": _threads_dispatch,
    "create_thread": _create_thread_dispatch,
    "close": _close_dispatch,
    "update_thread": _update_thread_dispatch,
    "update": _update_dispatch,
    "delete_thread": _delete_thread_dispatch,
    "delete_turn": _delete_turn_dispatch,
    "mark_read": _mark_read_dispatch,
}


# ── Registration ────────────────────────────────────────────────────


def register_agent_bus_tools(mcp: FastMCP) -> None:
    """Register the dispatch-style agent_bus tool on the MCP server instance."""

    @mcp.tool(title="Agent Bus")
    def agent_bus(tool: str, arguments: dict[str, Any] | str = "{}") -> Any:
        """Inter-agent message bus — threads, turns, read/reply coordination.

        tool: operation name (see table below)
        arguments: operation arguments as an object or a JSON string

        Operations:
          threads       (status?, tags?, lifecycle_state?)              — list threads; status: active|blocked|waiting|closed|all (default active); tags: AND-filter; lifecycle_state: pending|admitted|delivered|failed (exact match)
          create_thread (slug, summary?, tags?, lifecycle_state?, thread_id?) — create a thread without a turn; use lifecycle_state="pending" for lifecycle-managed threads that will be dispatched later
          fetch         (to?, thread?, last?, unread?, compact?, mark_read?)  — get turns; at least one of to/thread required
          get           (thread, turn_number)                           — get one specific turn
          post          (slug, to, subject, body, from_agent?, summary?, attachments?, tags?) — start a new thread (atomic: creates thread + first turn)
          reply         (thread, to, subject, body, after_turn, from_agent?, status?, mark_read?, close?, attachments?) — reply to a thread; close=true posts this as the final turn and closes the thread (marks all turns read)
          update        (thread, turn_number, body?, append?, subject?) — edit or append to an existing turn
          mark_read     (thread, turn_number)                           — mark a turn as read
          update_thread (thread, status?, summary?, tags?, from_agent?) — patch thread metadata (tags: omit=keep, []=clear, [...]=replace)
          close         (thread, summary?, mark_all_read?)              — close a thread (atomic: marks all turns read by default)
          delete_turn   (thread, turn_number, force?)                   — delete a single turn
          delete_thread (thread, force?)                                — delete an entire thread

        Thread response fields (ThreadDetail):
          id, slug, status, summary, turn_count, unread_count, tags, created_at, updated_at
          bus_lifecycle_state: str | null — lifecycle state for dispatch-managed threads
            (pending → admitted → delivered; null = not lifecycle-managed)
          dispatch_links: list — pipeline executions linked to this thread via dispatch-admit;
            each entry has: execution_id, pipeline_id, linked_at, terminal_status, delivery_at

        Tags (free-form strings on threads):
          Suggested `namespace:value` convention — nothing is enforced:
            project:<name>   — project scoping (e.g. project:claudeburst)
            type:<kind>      — intent (bug|feature|discussion|review|post-mortem)
            agent:<name>     — agent ownership/origin
            priority:<level> — if useful (high|medium|low)
          `threads(tags=[a,b])` matches threads that have ALL listed tags.

        Examples:
          agent_bus(tool="fetch", arguments='{"thread": "111", "last": 3, "compact": true}')
          agent_bus(tool="reply", arguments='{"thread": "111", "to": "cursor", "subject": "Re: topic", "body": "## Reply\\n...", "after_turn": 5}')
          agent_bus(tool="post", arguments='{"slug": "review-bug", "to": "cursor", "subject": "Bug found", "body": "## Details\\n...", "tags": ["project:ulg", "type:bug"]}')
          agent_bus(tool="threads", arguments='{"tags": ["project:claudeburst", "type:bug"]}')
          agent_bus(tool="threads", arguments='{"lifecycle_state": "pending"}')
          agent_bus(tool="create_thread", arguments='{"slug": "my-workflow", "lifecycle_state": "pending", "tags": ["project:ulg"]}')
          agent_bus(tool="update_thread", arguments='{"thread": "553", "tags": ["project:claudeburst", "type:restore"]}')
        """
        from ._agent_tools import _parse_dispatch_arguments

        handler = _AGENT_BUS_OPS.get(tool)
        if handler is None:
            return {
                "error": f"Unknown agent_bus tool {tool!r}. "
                f"Available: {sorted(_AGENT_BUS_OPS.keys())}"
            }
        t_prog, prog_timer = toolprogress_begin("agent_bus", inner_tool=tool)
        err: str | None = None
        try:
            parsed = _parse_dispatch_arguments(arguments)
            if parsed is None:
                return {
                    "error": (
                        "arguments must be an object or a JSON-encoded object; "
                        f"got {type(arguments).__name__}"
                    )
                }
            accepted = set(inspect.signature(handler).parameters)
            unknown = [k for k in parsed if k not in accepted]
            if unknown:
                record(
                    "mcp.agentbus.dispatch.rejected",
                    tool=tool,
                    unknown=",".join(sorted(unknown)),
                )
                return {
                    "error": (
                        f"{tool}: unsupported argument(s): "
                        f"{', '.join(sorted(unknown))}. "
                        f"Accepted: {sorted(accepted)}"
                    )
                }
            record("mcp.agentbus.dispatch", tool=tool)
            result = handler(**parsed)
            if (
                isinstance(result, dict)
                and "error" not in result
                and tool in ("post", "reply")
            ):
                result["_next"] = (
                    "If this message records a decision or surfaces an insight, "
                    "seed it as a cortex assert with "
                    'evidence_uris: ["agent-bus:THREAD_ID"]'
                )
            return result
        except Exception as exc:
            err = str(exc)
            raise
        finally:
            toolprogress_end(
                t_prog,
                prog_timer,
                "agent_bus",
                error=err,
                inner_tool=tool,
            )
