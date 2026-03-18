"""Agent-bus tools — first-class MCP interface to the Agent Bus service.

Provides typed, discoverable tools for agent-to-agent communication via
the Agent Bus REST API (http://agent-bus:8100). Replaces the raw
`local_api(service='agent-bus', ...)` passthrough pattern with ergonomic
wrappers that validate inputs and emit structured observability events.

All tools delegate HTTP I/O to `_relay()` from `local_api.py` — the same
function used by the `todo()` tool and other internal callers.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlencode

from fastmcp import FastMCP
from mcp_events import record
from request_profile import current_profile
from tool_access import CURSOR_SAFE_PROFILE

from .local_api import _relay

logger = logging.getLogger(__name__)
_PREVIEW_MAX_LAST = max(1, int(os.getenv("MCP_AGENT_BUS_PREVIEW_MAX_LAST", "20")))
_DETAIL_WINDOW = max(1, int(os.getenv("MCP_AGENT_BUS_DETAIL_WINDOW", "20")))


def _agent_bus_fetch_impl(
    *,
    to: str | None,
    thread: str | None,
    last: int,
    unread: bool,
    mark_read: bool,
    compact: bool,
) -> dict[str, Any]:
    if to is None and thread is None:
        return {"error": "agent_bus_fetch requires at least one of: to, thread"}

    params: dict[str, Any] = {}
    if thread is not None:
        params["thread"] = thread
    if to is not None:
        params["to"] = to
        if unread:
            params["unread"] = "true"
        if compact:
            params["compact"] = "true"
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
        "agent_bus_fetch: to=%s thread=%s mark_read=%s -> %d turns",
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


def _agent_bus_post_impl(
    *,
    slug: str,
    to: str,
    subject: str,
    body: str,
    from_agent: str,
    summary: str | None,
) -> dict[str, Any]:
    thread_payload: dict[str, Any] = {"slug": slug}
    if summary is not None:
        thread_payload["summary"] = summary
    thread_result = _relay("agent-bus", "POST", "/threads", body=thread_payload)
    if "error" in thread_result:
        return {"error": f"agent-bus error creating thread: {thread_result['error']}"}

    thread_id = thread_result.get("id", "")
    turn_payload = {
        "thread": thread_id,
        "from": from_agent,
        "to": to,
        "subject": subject,
        "body": body,
        "status": "open",
        "after_turn": 0,
    }
    turn_result = _relay("agent-bus", "POST", "/turns", body=turn_payload)
    if "error" in turn_result:
        return {"error": f"agent-bus error posting turn: {turn_result['error']}"}

    logger.info(
        "agent_bus_post: thread=%s slug=%s to=%s", thread_id, slug, to,
    )
    record(
        "mcp.agentbus.thread.created",
        thread=thread_id,
        slug=slug,
        to=to,
    )
    return {"thread": thread_result, "turn": turn_result}


def _agent_bus_reply_impl(
    *,
    thread: str,
    to: str,
    subject: str,
    body: str,
    after_turn: int,
    from_agent: str,
    status: str,
) -> dict[str, Any]:
    payload = {
        "thread": thread,
        "from": from_agent,
        "to": to,
        "subject": subject,
        "body": body,
        "status": status,
        "after_turn": after_turn,
    }
    result = _relay("agent-bus", "POST", "/turns", body=payload)

    if "error" in result:
        return {"error": f"agent-bus error: {result['error']}"}

    turn_number = result.get("turn_number") or result.get("id")
    logger.info("agent_bus_reply: thread=%s to=%s turn=%s", thread, to, turn_number)
    record(
        "mcp.agentbus.turn.posted",
        thread=thread,
        to=to,
        turn_number=str(turn_number) if turn_number is not None else "",
    )
    return result


def _agent_bus_threads_impl(*, status: str) -> dict[str, Any]:
    params = {} if status == "all" else {"status": status}
    qs = urlencode(params)
    path = f"/threads{f'?{qs}' if qs else ''}"
    result = _relay("agent-bus", "GET", path)

    if "error" in result:
        return {"error": f"agent-bus error: {result['error']}"}

    threads: list[Any] = (
        result if isinstance(result, list) else result.get("threads", [])
    )
    count = len(threads)
    logger.info("agent_bus_threads: status=%s -> %d threads", status, count)
    record("mcp.agentbus.threads.listed", status=status, count=count)
    return result


def _resolve_turn_id(
    *,
    thread: str,
    turn_number: int,
    last: int = 50,
) -> tuple[int | None, dict[str, Any] | None]:
    """Resolve (thread, turn_number) to turn id from a bounded recent window."""
    params = {"thread": thread, "last": last, "compact": "true"}
    qs = urlencode(params)
    result = _relay("agent-bus", "GET", f"/turns?{qs}")
    if isinstance(result, dict) and "error" in result:
        return None, {"error": f"agent-bus error: {result['error']}"}

    turns: list[Any] = result if isinstance(result, list) else result.get("turns", [])
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        raw = turn.get("turn_number")
        try:
            if raw is not None and int(raw) == turn_number:
                return int(turn["id"]), None
        except (TypeError, ValueError):
            continue

    return (
        None,
        {
            "error": f"Turn {turn_number} not found in thread {thread} "
            f"(searched last {last} turns)"
        },
    )


def _agent_bus_turn_update_impl(
    *,
    thread: str,
    turn_number: int,
    body: str | None,
    append: str | None,
    subject: str | None,
) -> dict[str, Any]:
    """Resolve thread+turn_number to turn_id, then PATCH."""
    turn_id, resolve_error = _resolve_turn_id(thread=thread, turn_number=turn_number)
    if resolve_error is not None:
        return resolve_error

    patch_body: dict[str, str] = {}
    if body is not None:
        patch_body["body"] = body
    if append is not None:
        patch_body["append"] = append
    if subject is not None:
        patch_body["subject"] = subject

    patch_result = _relay("agent-bus", "PATCH", f"/turns/{turn_id}", body=patch_body)
    if isinstance(patch_result, dict) and "error" in patch_result:
        return {"error": f"agent-bus error: {patch_result['error']}"}

    logger.info(
        "agent_bus_turn_update: thread=%s turn=%d id=%d", thread, turn_number, turn_id,
    )
    record(
        "mcp.agentbus.turn.updated",
        thread=thread,
        turn_number=turn_number,
        has_append=append is not None,
    )

    if append is not None and isinstance(patch_result, dict):
        full_body = patch_result.pop("body", "")
        if full_body is None:
            full_body = ""
        patch_result["body_length"] = len(full_body)
        patch_result["body_tail"] = full_body[-200:]

    return patch_result


def _agent_bus_update_thread_impl(
    *,
    thread: str,
    status: str | None,
    summary: str | None,
) -> dict[str, Any]:
    payload: dict[str, str] = {}
    if status is not None:
        payload["status"] = status
    if summary is not None:
        payload["summary"] = summary

    if not payload:
        return {
            "error": "agent_bus_update_thread requires at least one of: status, summary"
        }

    result = _relay("agent-bus", "PATCH", f"/threads/{thread}", body=payload)

    if "error" in result:
        return {"error": f"agent-bus error: {result['error']}"}

    logger.info("agent_bus_update_thread: thread=%s status=%s", thread, status)
    record("mcp.agentbus.thread.updated", thread=thread, status=status or "")
    return result


def _agent_bus_delete_turn_impl(
    *,
    thread: str,
    turn_number: int,
    force: bool,
) -> dict[str, Any]:
    """Resolve thread+turn_number to turn_id, then DELETE."""
    turn_id, resolve_error = _resolve_turn_id(thread=thread, turn_number=turn_number)
    if resolve_error is not None:
        return resolve_error

    force_params = urlencode({"force": "true"}) if force else ""
    path = f"/turns/{turn_id}?{force_params}" if force_params else f"/turns/{turn_id}"
    delete_result = _relay("agent-bus", "DELETE", path)
    if isinstance(delete_result, dict) and "error" in delete_result:
        return {"error": f"agent-bus error: {delete_result['error']}"}

    logger.info(
        "agent_bus_delete_turn: thread=%s turn=%d id=%d force=%s",
        thread, turn_number, turn_id, force,
    )
    record(
        "mcp.agentbus.turn.deleted",
        thread=thread,
        turn_number=turn_number,
        force=force,
    )
    return delete_result


def _agent_bus_delete_thread_impl(
    *,
    thread: str,
    force: bool,
) -> dict[str, Any]:
    """Delete a thread and all its turns via the agent-bus service."""
    params = {"force": "true"} if force else {}
    qs = urlencode(params)
    path = f"/threads/{thread}{f'?{qs}' if qs else ''}"
    result = _relay("agent-bus", "DELETE", path)

    if isinstance(result, dict) and "error" in result:
        return {"error": f"agent-bus error: {result['error']}"}

    deleted_turns = result.get("deleted_turns", 0) if isinstance(result, dict) else 0
    logger.info(
        "agent_bus_delete_thread: thread=%s force=%s deleted_turns=%d",
        thread, force, deleted_turns,
    )
    record(
        "mcp.agentbus.thread.deleted",
        thread=thread,
        force=force,
        deleted_turns=deleted_turns,
    )
    return result


def register_agent_bus_tools(mcp: FastMCP) -> None:
    """Register first-class agent-bus tools on the MCP server instance."""

    @mcp.tool()
    def agent_bus_fetch(
        to: str | None = None,
        thread: str | None = None,
        last: int = 5,
        unread: bool = True,
        mark_read: bool = True,
        compact: bool = True,
    ) -> dict[str, Any]:
        """Fetch turns from the Agent Bus — inbox or thread history.

        This full-surface tool is disabled in cursor_safe profile because it can
        return large markdown bodies. Cursor-safe clients must use
        ``agent_bus_fetch_preview`` followed by ``agent_bus_turn_get``.

        Inbox mode (to provided): returns unread turns addressed to an agent.
        Thread mode (thread provided): returns full history for a thread.
        At least one of to or thread is required.

        agent_bus_fetch(to='web', last=1)       # next unread for web
        agent_bus_fetch(thread='034')           # all turns in thread 034

        Args:
            to: Agent name for inbox mode (e.g. 'web', 'cursor').
            thread: Thread ID for thread history mode (e.g. '034').
            last: Maximum turns to return (default 5).
            unread: Inbox mode only — filter to unread turns (default True).
            mark_read: Mark returned turns as read (default True).
            compact: Omit turn bodies, return subject and metadata only (default False).

        Returns:
            Agent-bus response with turns list, or {"error": "<message>"}.
        """
        profile = current_profile()
        if profile == CURSOR_SAFE_PROFILE:
            reason = (
                "agent_bus_fetch is disabled for cursor_safe profile. "
                "Use agent_bus_fetch_preview + agent_bus_turn_get."
            )
            record(
                "mcp.profile.tool.denied",
                profile=profile,
                tool="agent_bus_fetch",
                entrypoint="direct",
                reason=reason,
            )
            return {"error": reason}

        return _agent_bus_fetch_impl(
            to=to,
            thread=thread,
            last=last,
            unread=unread,
            mark_read=mark_read,
            compact=compact,
        )

    @mcp.tool()
    def agent_bus_fetch_preview(
        to: str | None = None,
        thread: str | None = None,
        last: int = 10,
        unread: bool = True,
        mark_read: bool = False,
    ) -> dict[str, Any]:
        """Fetch bounded compact turn previews for Cursor-safe workflows.

        Returns metadata and subjects only (no turn body text). Detail expansion
        is a second explicit step via ``agent_bus_turn_get``.
        """
        safe_last = max(1, min(last, _PREVIEW_MAX_LAST))
        record(
            "mcp.agentbus.preview.called",
            to=to or "",
            thread=thread or "",
            requested_last=last,
            effective_last=safe_last,
        )
        result = _agent_bus_fetch_impl(
            to=to,
            thread=thread,
            last=safe_last,
            unread=unread,
            mark_read=mark_read,
            compact=True,
        )
        if not (isinstance(result, dict) and "error" in result):
            turns = result if isinstance(result, list) else result.get("turns", [])
            record(
                "mcp.agentbus.preview.completed",
                to=to or "",
                thread=thread or "",
                count=len(turns) if isinstance(turns, list) else 0,
            )
        return result

    @mcp.tool()
    def agent_bus_turn_get(
        thread: str,
        turn_number: int,
    ) -> dict[str, Any]:
        """Fetch one turn body from a bounded recent-thread window.

        Until Agent Bus exposes a first-class single-turn endpoint, this tool
        performs a bounded lookup over recent turns and returns one match.
        """
        params = {
            "thread": thread,
            "last": _DETAIL_WINDOW,
            "compact": "false",
        }
        qs = urlencode(params)
        result = _relay("agent-bus", "GET", f"/turns?{qs}")
        if isinstance(result, dict) and "error" in result:
            return {"error": f"agent-bus error: {result['error']}"}

        turns: list[Any] = result.get("turns", []) if isinstance(result, dict) else result
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            raw_turn_number = turn.get("turn_number")
            try:
                if raw_turn_number is not None and int(raw_turn_number) == turn_number:
                    record(
                        "mcp.agentbus.turn.detail.fetched",
                        thread=thread,
                        turn_number=turn_number,
                        window=_DETAIL_WINDOW,
                    )
                    return {"turn": turn, "window": _DETAIL_WINDOW}
            except (TypeError, ValueError):
                continue

        return {
            "error": (
                f"Turn {turn_number} not found in recent window "
                f"(last {_DETAIL_WINDOW} turns)."
            )
        }

    @mcp.tool()
    def agent_bus_post(
        slug: str,
        to: str,
        subject: str,
        body: str,
        from_agent: str = "web",
        summary: str | None = None,
    ) -> dict[str, Any]:
        """Create a new Agent Bus thread with auto-assigned numeric ID and post the first turn.

        Use this instead of agent_bus_reply when starting a new conversation.
        The thread ID is auto-assigned (e.g. '057') — never invent thread IDs.

        Args:
            slug: Human-readable thread name (e.g. 'vram-investigation').
            to: Recipient agent name (e.g. 'cursor', 'web').
            subject: Short summary shown in thread listings.
            body: Full turn content in Markdown.
            from_agent: Sender identity (default 'web').
            summary: Optional thread summary.

        Returns:
            {"thread": {...}, "turn": {...}} with auto-assigned thread ID,
            or {"error": "<message>"}.
        """
        return _agent_bus_post_impl(
            slug=slug,
            to=to,
            subject=subject,
            body=body,
            from_agent=from_agent,
            summary=summary,
        )

    @mcp.tool()
    def agent_bus_reply(
        thread: str,
        to: str,
        subject: str,
        body: str,
        after_turn: int,
        from_agent: str = "web",
        status: str = "open",
    ) -> dict[str, Any]:
        """Post a turn to an existing Agent Bus thread.

        after_turn prevents out-of-order posts by asserting the turn_number
        this reply follows. Do NOT use this to create new threads — use
        agent_bus_post instead.

        Args:
            thread: Thread ID to post into (e.g. '034').
            to: Recipient agent name (e.g. 'cursor', 'web').
            subject: Short summary shown in thread listings.
            body: Full turn content in Markdown.
            after_turn: The turn_number this reply follows.
            from_agent: Sender identity (default 'web').
            status: Thread status hint — 'open' (default) or 'resolved'.

        Returns:
            {"id": <turn_id>, "turn_number": <n>, ...} or {"error": "<message>"}.
        """
        return _agent_bus_reply_impl(
            thread=thread,
            to=to,
            subject=subject,
            body=body,
            after_turn=after_turn,
            from_agent=from_agent,
            status=status,
        )

    @mcp.tool()
    def agent_bus_turn_update(
        thread: str,
        turn_number: int,
        body: str | None = None,
        append: str | None = None,
        subject: str | None = None,
    ) -> dict[str, Any]:
        """Update an unread turn's body or subject, or append to its body.

        Only works while the turn has not been read (read_at is null).
        Use ``append`` to concatenate content to the existing body — each
        call is committed immediately, surviving disconnections.

        Typical incremental-build pattern:
            1. agent_bus_reply(...) → creates turn N with initial content
            2. agent_bus_turn_update(thread, N, append="## Section 2\\n...")
            3. agent_bus_turn_update(thread, N, append="## Section 3\\n...")
            4. agent_bus_turn_update(thread, N, subject="Final title")

        If the session disconnects after step 2, sections 1-2 are committed.

        Args:
            thread: Thread ID (e.g. '053').
            turn_number: The turn_number within the thread (e.g. 13).
            body: Replace the entire body (mutually exclusive with append).
            append: Concatenate this text to the existing body.
            subject: Replace the subject line.

        Returns:
            Updated turn object, or {"error": "<message>"}.
        """
        if body is None and append is None and subject is None:
            return {"error": "At least one of body, append, or subject required"}
        if body is not None and append is not None:
            return {"error": "Cannot specify both body (replace) and append"}
        return _agent_bus_turn_update_impl(
            thread=thread,
            turn_number=turn_number,
            body=body,
            append=append,
            subject=subject,
        )

    @mcp.tool()
    def agent_bus_threads(
        status: str = "active",
    ) -> dict[str, Any]:
        """List Agent Bus threads, optionally filtered by status.

        Args:
            status: 'active' (default), 'closed', 'blocked', 'waiting', or 'all'.

        Returns:
            Agent-bus response with threads list, or {"error": "<message>"}.
        """
        return _agent_bus_threads_impl(status=status)

    @mcp.tool()
    def agent_bus_update_thread(
        thread: str,
        status: str | None = None,
        summary: str | None = None,
    ) -> dict[str, Any]:
        """Update Agent Bus thread metadata — status and/or summary.

        Primary use: thread close protocol. Always set summary when closing.

        agent_bus_update_thread(
            thread='034',
            status='closed',
            summary='First-class agent-bus MCP tools shipped...',
        )

        Args:
            thread: Thread ID to update (e.g. '034').
            status: New status — 'active', 'closed', 'blocked', or 'waiting'.
            summary: 1-2 sentence summary. Required when closing a thread.

        Returns:
            Updated thread object, or {"error": "<message>"}.
        """
        return _agent_bus_update_thread_impl(
            thread=thread,
            status=status,
            summary=summary,
        )

    @mcp.tool()
    def agent_bus_delete_thread(
        thread: str,
        force: bool = False,
    ) -> dict[str, Any]:
        """Delete a thread and all its turns.

        By default, refuses to delete threads with read turns (safety check).
        Use force=True to delete regardless.

        Args:
            thread: Thread ID to delete (e.g. '054').
            force: If True, delete even if turns have been read.

        Returns:
            {"deleted_turns": <count>, "thread": "<id>"},
            or {"error": "<message>"}.
        """
        return _agent_bus_delete_thread_impl(thread=thread, force=force)

    @mcp.tool()
    def agent_bus_delete_turn(
        thread: str,
        turn_number: int,
        force: bool = False,
    ) -> dict[str, Any]:
        """Delete a single turn from an Agent Bus thread.

        By default, refuses to delete turns that have been read (read_at set).
        Use force=True to delete regardless. Does not auto-delete the parent
        thread if it becomes empty.

        Args:
            thread: Thread ID containing the turn (e.g. '053').
            turn_number: The turn_number within the thread (e.g. 14).
            force: If True, delete even if the turn has been read.

        Returns:
            {"deleted_turn": <id>, "thread": "<id>", "turn_number": <n>},
            or {"error": "<message>"}.
        """
        return _agent_bus_delete_turn_impl(
            thread=thread, turn_number=turn_number, force=force,
        )
