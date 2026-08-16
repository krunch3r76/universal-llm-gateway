"""Thread read routes: list, get, summary, export."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException, Query, Response, status
from openapi_mcp.binding import x_mcp

from ...db import (
    get_thread,
    get_thread_summary,
    get_thread_turns_asc,
    list_threads_v2,
    normalize_thread_id,
)
from ...turns_models import (
    ThreadDetail,
    ThreadListResponse,
    ThreadStatus,
    ThreadSummaryResponse,
)
from . import router


def _thread_detail(row: dict[str, Any]) -> ThreadDetail:
    """Convert a thread aggregate row to the typed API response model."""
    return ThreadDetail(
        id=row["id"],
        slug=row["slug"],
        status=row["status"],
        summary=row["summary"],
        turn_count=row["turn_count"],
        unread_count=row["unread_count"],
        last_subject=row["last_subject"],
        last_turn_from=row["last_turn_from"],
        last_turn_to=row["last_turn_to"],
        tags=row.get("tags", []) or [],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        bus_lifecycle_state=row.get("bus_lifecycle_state"),
        parent_thread=row.get("parent_thread"),
        lane_role=row.get("lane_role"),
    )


@router.get(
    "/threads",
    response_model=ThreadListResponse,
    openapi_extra=x_mcp("threads", tool="agent_bus"),
)
async def list_threads_route(
    thread_status: ThreadStatus | None = Query(None, alias="status"),
    tags: list[str] | None = Query(None),
    lifecycle_state: str | None = Query(None),
    has_unread: bool | None = Query(
        None,
        description=(
            "When true, only return threads with at least one unread turn. "
            "When false, only return threads with zero unread turns. Omit "
            "for no unread filtering (default)."
        ),
    ),
    limit: int | None = Query(
        None,
        ge=1,
        le=500,
        description=(
            "Cap the result count after ordering by most recent update. "
            "Boot consumers pair this with `has_unread=true&limit=10` to "
            "deliver only the inbound attention list without paginating "
            "the full active-thread set."
        ),
    ),
    query: str | None = Query(
        None,
        description=(
            "Case-insensitive substring match over slug, summary, and "
            "last_subject. Clamped to 200 characters server-side."
        ),
    ),
) -> ThreadListResponse:
    """List threads with optional status + AND-tag + lifecycle_state filtering.

    `tags`: repeat the param to filter on multiple tags (AND semantics).
    Example: `GET /threads?tags=project:X&tags=type:bug`.

    `lifecycle_state`: filter by exact lifecycle state value.
    Example: `GET /threads?lifecycle_state=pending`.

    `has_unread` + `limit`: compact attention projection.
    Example: `GET /threads?status=active&has_unread=true&limit=10`.

    `query`: free-text lookup composed with other filters.
    Example: `GET /threads?query=wave-b&status=active`.
    """
    rows = list_threads_v2(
        status=thread_status,
        tags=tags,
        lifecycle_state=lifecycle_state,
        has_unread=has_unread,
        limit=limit,
        query=query,
    )
    return ThreadListResponse(threads=[_thread_detail(r) for r in rows])


@router.get(
    "/threads/{thread_id}",
    response_model=ThreadDetail,
    openapi_extra=x_mcp("thread_get", tool="agent_bus"),
)
async def get_thread_route(thread_id: str) -> ThreadDetail:
    """Fetch one thread by id after normalizing numeric aliases first."""
    thread_id = normalize_thread_id(thread_id)
    row = get_thread(thread_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return _thread_detail(row)


@router.get(
    "/threads/{thread_id}/summary",
    response_model=ThreadSummaryResponse,
)
async def get_thread_summary_route(
    thread_id: str, recent: int = Query(3)
) -> ThreadSummaryResponse:
    """Return thread summary plus a bounded list of most recent subjects."""
    thread_id = normalize_thread_id(thread_id)
    row = get_thread_summary(thread_id, recent=recent)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return ThreadSummaryResponse(
        id=row["id"],
        slug=row["slug"],
        status=row["status"],
        summary=row["summary"],
        turn_count=row["turn_count"],
        unread_count=row["unread_count"],
        recent_subjects=row["recent_subjects"],
        tags=row.get("tags", []) or [],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


@router.get("/threads/{thread_id}/export")
async def export_thread_route(thread_id: str) -> Response:
    """Reconstruct a human-readable markdown document from turns."""
    thread_id = normalize_thread_id(thread_id)
    thread = get_thread(thread_id)
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    turns = get_thread_turns_asc(thread_id)

    lines: list[str] = [f"# Thread {thread['id']} - {thread['slug']}\n"]
    if thread.get("summary"):
        lines.append(f"> {thread['summary']}\n")
    lines.append(f"Status: {thread['status']}  |  Turns: {len(turns)}\n")

    for t in turns:
        lines.append("---\n")
        lines.append(
            f"## Turn {t['turn_number']} - {t['from_agent']} - {t['created_at']} UTC\n"
        )
        lines.append(f"**To:** {t['to_agent']}\n")
        if t.get("subject"):
            lines.append(f"**Subject:** {t['subject']}\n")
        lines.append(f"\n{t['body']}\n")
        atts = t.get("attachments")
        if atts:
            lines.append("\n**Attachments:**\n")
            for a in atts:
                size = f" ({a['size_bytes']} bytes)" if a.get("size_bytes") else ""
                lines.append(f"- `{a['filename']}`{size} — {a['path']}\n")

    content = "\n".join(lines)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{thread_id}-{thread["slug"]}.md"'
            )
        },
    )


__all__ = ["_thread_detail"]
