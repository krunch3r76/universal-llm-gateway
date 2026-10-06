"""Thread read routes: list, get, summary, export."""

from __future__ import annotations

import asyncio
import time
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
from ...resume_envelope import build_resume_envelope
from ...thread_classification import classify_thread
from ...turns_models import (
    ThreadDetail,
    ThreadListResponse,
    ThreadStatus,
    ThreadSummaryResponse,
    UnreadBasis,
)
from . import router

_RESUME_ENVELOPE_TIMEOUT_S = 10.0


_UNREAD_COUNT_DESC = (
    "Unstamped (read_at null) non-superseded turns. Not a recipient inbox, "
    "not CSE/chat consumption, and not send-latency or correspondent lag. "
    "Pass to= for a recipient-scoped count that matches fetch_unread."
)


def _unread_basis(row: dict[str, Any]) -> UnreadBasis | None:
    raw = row.get("unread_basis")
    if not isinstance(raw, dict):
        return None
    return UnreadBasis(
        basis=str(raw["basis"]),
        recipient=raw.get("recipient"),
        includes_superseded=bool(raw["includes_superseded"]),
        as_of=datetime.fromisoformat(str(raw["as_of"]).replace("Z", "+00:00")),
        source=str(raw["source"]),
    )


def _thread_detail(row: dict[str, Any]) -> ThreadDetail:
    """Convert a thread aggregate row to the typed API response model."""
    return ThreadDetail(
        id=row["id"],
        slug=row["slug"],
        status=row["status"],
        summary=row["summary"],
        turn_count=row["turn_count"],
        unread_count=row["unread_count"],
        unread_basis=_unread_basis(row),
        last_subject=row["last_subject"],
        last_turn_from=row["last_turn_from"],
        last_turn_to=row["last_turn_to"],
        tags=row.get("tags", []) or [],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        bus_lifecycle_state=row.get("bus_lifecycle_state"),
        parent_thread=row.get("parent_thread"),
        lane_role=row.get("lane_role"),
        cse_chat_url=row.get("cse_chat_url"),
        cse_registration_id=row.get("cse_registration_id"),
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
            "When true, only return threads with unread_count > 0. "
            "When false, only return threads with unread_count == 0. "
            "Omit for no unread filtering (default). " + _UNREAD_COUNT_DESC
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
    to: str | None = Query(
        None,
        description=(
            "Recipient seat. When set, unread_count and has_unread use the "
            "same recipient_in_clause rule as fetch_unread (include_team "
            "except kaywan). " + _UNREAD_COUNT_DESC
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
        to=to,
    )
    return ThreadListResponse(threads=[_thread_detail(r) for r in rows])


@router.get(
    "/threads/{thread_id}",
    response_model=ThreadDetail,
    openapi_extra=x_mcp("thread_get", tool="agent_bus"),
)
async def get_thread_route(
    thread_id: str,
    include_resume: bool = Query(
        False,
        description=(
            "When true, spine=root continuity threads include "
            "resume_envelope (last-session verbal tape pour). Work threads omit it."
        ),
    ),
    to: str | None = Query(
        None,
        description=(
            "Recipient seat. When set, unread_count uses the same "
            "recipient_in_clause rule as fetch_unread. " + _UNREAD_COUNT_DESC
        ),
    ),
) -> ThreadDetail:
    """Fetch one thread by id after normalizing numeric aliases first."""
    thread_id = normalize_thread_id(thread_id)
    row = get_thread(thread_id, to=to)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    detail = _thread_detail(row)
    if include_resume and classify_thread(detail.tags)["spine"] == "root":
        detail.resume_envelope = await asyncio.to_thread(
            build_resume_envelope,
            thread_id,
            deadline=time.monotonic() + _RESUME_ENVELOPE_TIMEOUT_S,
        )
    return detail


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
        unread_basis=_unread_basis(row),
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
