"""Thread lifecycle routes: create, update, rename, close, delete."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Query, status
from openapi_mcp.binding import x_mcp

from ...db import (
    ThreadHasReadTurns,
    add_tags,
    close_thread,
    create_thread,
    delete_thread,
    normalize_thread_id,
    remove_tags,
    rename_thread,
    update_thread,
)
from ...enrollment_guard import EnrollmentTagError, enrollment_denied_http
from ...thread_classification import ThreadClassificationError
from ...turns_models import (
    ThreadClose,
    ThreadCreate,
    ThreadDetail,
    ThreadRename,
    ThreadUpdate,
)
from . import router
from .detail import _thread_detail


def _raise_enrollment_denied(exc: BaseException) -> None:
    mapped = enrollment_denied_http(exc)
    if mapped is None:
        return
    status_code, detail = mapped
    raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post(
    "/threads",
    status_code=status.HTTP_201_CREATED,
    response_model=ThreadDetail,
    openapi_extra=x_mcp("create_thread", tool="agent_bus"),
)
async def create_thread_route(body: ThreadCreate) -> ThreadDetail:
    """Create one thread using explicit or auto-generated thread identifiers."""
    if body.id is not None:
        body.id = normalize_thread_id(body.id)
    try:
        row = create_thread(
            thread_id=body.id,
            slug=body.slug,
            summary=body.summary,
            tags=body.tags,
            lifecycle_state=body.lifecycle_state,
            enroll_charter_runner=body.enroll_charter_runner,
        )
    except (EnrollmentTagError, ThreadClassificationError) as exc:
        _raise_enrollment_denied(exc)
        raise
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Thread {body.id} already exists",
        )
    return _thread_detail(row)


@router.patch(
    "/threads/{thread_id}/close",
    response_model=ThreadDetail,
    openapi_extra=x_mcp("close", tool="agent_bus"),
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
    openapi_extra=x_mcp("update_thread", tool="agent_bus"),
)
async def update_thread_route(thread_id: str, body: ThreadUpdate) -> ThreadDetail:
    thread_id = normalize_thread_id(thread_id)
    if body.add_tags is not None and body.tags is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "add_tags and tags (replace) are mutually exclusive",
                "reason": "tag_op_conflict",
            },
        )
    if body.remove_tags is not None and body.tags is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "remove_tags and tags (replace) are mutually exclusive",
                "reason": "tag_op_conflict",
            },
        )
    try:
        if body.add_tags is not None:
            row = add_tags(
                thread_id,
                body.add_tags,
                enroll_charter_runner=body.enroll_charter_runner,
            )
        elif body.remove_tags is not None:
            row = remove_tags(thread_id, body.remove_tags)
        else:
            row = update_thread(
                thread_id,
                status=body.status,
                summary=body.summary,
                tags=body.tags,
                enroll_charter_runner=body.enroll_charter_runner,
            )
    except (EnrollmentTagError, ThreadClassificationError) as exc:
        _raise_enrollment_denied(exc)
        raise
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    if body.add_tags is not None or body.remove_tags is not None:
        if body.status is not None or body.summary is not None:
            row = update_thread(
                thread_id,
                status=body.status,
                summary=body.summary,
                tags=None,
                enroll_charter_runner=body.enroll_charter_runner,
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


@router.delete(
    "/threads/{thread_id}",
    openapi_extra=x_mcp("delete_thread", tool="agent_bus"),
)
async def delete_thread_route(
    thread_id: str,
    force: bool = Query(False),
) -> dict[str, Any]:
    """Delete a thread, requiring force when acknowledged turns already exist."""
    import sqlite3

    thread_id = normalize_thread_id(thread_id)
    try:
        return delete_thread(thread_id, force=force)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "lane_parent_delete_restricted",
                "message": str(exc),
                "retryable": False,
                "source": "agent_bus_store.routes.threads",
                "data": {"thread_id": thread_id},
            },
        ) from exc
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


__all__ = ["_raise_enrollment_denied"]
