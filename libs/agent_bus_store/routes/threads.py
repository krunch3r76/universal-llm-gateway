"""Thread routes - CRUD for conversation threads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ..auth import require_token
from ..db import (
    ThreadHasReadTurns,
    close_thread,
    create_thread,
    create_thread_with_turn,
    delete_thread,
    get_thread,
    get_thread_summary,
    get_thread_turns_asc,
    list_threads_v2,
    normalize_thread_id,
    rename_thread,
    update_thread,
)
from ..db.turns import UnreadTurnsExist
from ..turns_models import (
    ThreadClose,
    ThreadCreate,
    ThreadDetail,
    ThreadListResponse,
    ThreadRename,
    ThreadStatus,
    ThreadSummaryResponse,
    ThreadUpdate,
    ThreadWithTurnCreate,
    ThreadWithTurnCreated,
    TurnCreated,
)

router = APIRouter(dependencies=[Depends(require_token)])


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
    )


@router.get(
    "/threads",
    response_model=ThreadListResponse,
)
async def list_threads_route(
    thread_status: ThreadStatus | None = Query(None, alias="status"),
    tags: list[str] | None = Query(None),
) -> ThreadListResponse:
    """List threads with optional status + AND-tag filtering.

    `tags`: repeat the param to filter on multiple tags (AND semantics).
    Example: `GET /threads?tags=project:X&tags=type:bug`.
    """
    rows = list_threads_v2(status=thread_status, tags=tags)
    return ThreadListResponse(threads=[_thread_detail(r) for r in rows])


@router.get(
    "/threads/{thread_id}",
    response_model=ThreadDetail,
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


@router.post(
    "/threads",
    status_code=status.HTTP_201_CREATED,
    response_model=ThreadDetail,
)
async def create_thread_route(body: ThreadCreate) -> ThreadDetail:
    """Create one thread using explicit or auto-generated thread identifiers."""
    if body.id is not None:
        body.id = normalize_thread_id(body.id)
    row = create_thread(
        thread_id=body.id, slug=body.slug, summary=body.summary, tags=body.tags
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Thread {body.id} already exists",
        )
    return _thread_detail(row)


@router.post(
    "/threads/with-turn",
    status_code=status.HTTP_201_CREATED,
    response_model=ThreadWithTurnCreated,
)
async def create_thread_with_turn_route(
    body: ThreadWithTurnCreate,
) -> ThreadWithTurnCreated:
    """Atomically create a thread and its first turn in one transaction."""
    att_dicts = [a.model_dump() for a in body.attachments] if body.attachments else None
    try:
        thread_row, turn_id, ts, turn_number = create_thread_with_turn(
            slug=body.slug,
            summary=body.summary,
            from_agent=body.from_agent,
            to_agent=body.to,
            subject=body.subject,
            body=body.body,
            status=body.status,
            after_turn=body.after_turn,
            attachments=att_dicts,
            tags=body.tags,
        )
    except UnreadTurnsExist as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "unread_turns_exist",
                "message": "Read all turns addressed to you before posting",
                "unread_turns": e.unread,
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return ThreadWithTurnCreated(
        thread=_thread_detail(thread_row),
        turn=TurnCreated(
            id=turn_id,
            thread=thread_row["id"],
            turn_number=turn_number,
            created_at=datetime.fromisoformat(ts),
        ),
    )


@router.patch(
    "/threads/{thread_id}/close",
    response_model=ThreadDetail,
)
async def close_thread_route(thread_id: str, body: ThreadClose) -> ThreadDetail:
    """Atomically close a thread: mark all turns read + set status + summary."""
    thread_id = normalize_thread_id(thread_id)
    row = close_thread(
        thread_id, summary=body.summary, mark_all_read=body.mark_all_read
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return _thread_detail(row)


@router.patch(
    "/threads/{thread_id}",
    response_model=ThreadDetail,
)
async def update_thread_route(thread_id: str, body: ThreadUpdate) -> ThreadDetail:
    thread_id = normalize_thread_id(thread_id)
    row = update_thread(
        thread_id, status=body.status, summary=body.summary, tags=body.tags
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return _thread_detail(row)


@router.post(
    "/threads/{thread_id}/rename",
    response_model=ThreadDetail,
)
async def rename_thread_route(thread_id: str, body: ThreadRename) -> ThreadDetail:
    """Rename a thread id while preserving associated turns and messages."""
    thread_id = normalize_thread_id(thread_id)
    try:
        row = rename_thread(thread_id, new_id=body.new_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return _thread_detail(row)


@router.delete("/threads/{thread_id}")
async def delete_thread_route(
    thread_id: str,
    force: bool = Query(False),
) -> dict[str, Any]:
    """Delete a thread, requiring force when acknowledged turns already exist."""
    thread_id = normalize_thread_id(thread_id)
    try:
        return delete_thread(thread_id, force=force)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    except ThreadHasReadTurns as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Thread has {e.read_count} read turn(s) - use force=true",
        )
