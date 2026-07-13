"""Thread listing and creation dispatchers."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from mcp_events import record

from ._shared import relay

logger = logging.getLogger(__name__)


def _threads_impl(
    *,
    status: str,
    tags: list[str] | None = None,
    lifecycle_state: str | None = None,
    last: int | None = None,
    limit: int | None = None,
    has_unread: bool | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    params: list[tuple[str, str]] = []
    if status != "all":
        params.append(("status", status))
    tag_list = [t.strip() for t in (tags or []) if t and t.strip()]
    for tag in tag_list:
        params.append(("tags", tag))
    if lifecycle_state:
        params.append(("lifecycle_state", lifecycle_state))
    effective_limit = limit if limit is not None else last
    if effective_limit is not None:
        params.append(("limit", str(effective_limit)))
    if has_unread is not None:
        params.append(("has_unread", "true" if has_unread else "false"))
    if query:
        params.append(("query", query))
    qs = urlencode(params)
    path = f"/threads?{qs}" if qs else "/threads"
    result = relay("agent-bus", "GET", path)

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
    result = relay("agent-bus", "POST", "/threads", body=payload)
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


def _threads_dispatch(
    *,
    status: str = "active",
    tags: list[str] | None = None,
    lifecycle_state: str | None = None,
    last: int | None = None,
    limit: int | None = None,
    has_unread: bool | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    return _threads_impl(
        status=status,
        tags=tags,
        lifecycle_state=lifecycle_state,
        last=last,
        limit=limit,
        has_unread=has_unread,
        query=query,
    )


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
