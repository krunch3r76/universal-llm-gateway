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
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from mcp_events import record

from .local_api import _relay

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)


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
    path = f"/threads?{qs}" if qs else "/threads"
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
        return _agent_bus_fetch_impl(
            to=to,
            thread=thread,
            last=last,
            unread=unread,
            mark_read=mark_read,
            compact=compact,
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
        """Post a turn to an Agent Bus thread.

        after_turn prevents out-of-order posts by asserting the turn_number
        this reply follows.

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
