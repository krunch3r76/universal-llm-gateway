"""Atomic create-thread-plus-first-turn route."""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import HTTPException, status
from openapi_mcp.binding import x_mcp

from ...body_auto_spill import build_turn_created
from ...enrollment_guard import EnrollmentTagError
from ...thread_classification import ThreadClassificationError
from ...turns_models import (
    ThreadWithTurnCreate,
    ThreadWithTurnCreated,
    post_continuation_misuse_error,
)
from . import router
from .crud import _raise_enrollment_denied
from .detail import _thread_detail
from .new_thread_send import run_new_thread_send


@router.post(
    "/threads/with-turn",
    status_code=status.HTTP_201_CREATED,
    response_model=ThreadWithTurnCreated,
    openapi_extra=x_mcp("post", tool="agent_bus"),
)
async def create_thread_with_turn_route(
    body: ThreadWithTurnCreate,
) -> ThreadWithTurnCreated:
    """Atomically create a thread and its first turn in one transaction."""
    if error_detail := post_continuation_misuse_error(body.slug, body.after_turn):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail,
        )
    att_dicts = [a.model_dump() for a in body.attachments] if body.attachments else None
    try:
        thread_row, turn_id, ts, turn_number, prepared = await asyncio.to_thread(
            run_new_thread_send,
            route="with_turn",
            strict_slug=False,
            slug=body.slug,
            summary=body.summary,
            tags=body.tags,
            lifecycle_state=body.lifecycle_state,
            enroll_charter_runner=body.enroll_charter_runner,
            from_agent=body.from_agent,
            to_agent=body.to,
            subject=body.subject,
            body=body.body,
            turn_status=body.status,
            attachments=att_dicts,
            allow_long_body=body.allow_long_body,
            lane_bind_body=None,
        )
    except (EnrollmentTagError, ThreadClassificationError) as exc:
        _raise_enrollment_denied(exc)
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return ThreadWithTurnCreated(
        thread=_thread_detail(thread_row),
        turn=build_turn_created(
            prepared,
            turn_id=turn_id,
            thread=thread_row["id"],
            turn_number=turn_number,
            created_at=datetime.fromisoformat(ts),
            from_agent=body.from_agent,
            to_agent=body.to,
            subject=body.subject,
        ),
    )
