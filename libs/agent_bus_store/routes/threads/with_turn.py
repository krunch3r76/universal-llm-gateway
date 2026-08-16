"""Atomic create-thread-plus-first-turn route."""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException, status
from openapi_mcp.binding import x_mcp

from ...body_auto_spill import PreparedBody, build_turn_created, spill_error_http
from ...db import create_thread_with_turn
from ...db.turns import UnreadTurnsExist
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
from .send_prep import _spill_transformer


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
    spill_holder: dict[str, PreparedBody] = {}
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
            lifecycle_state=body.lifecycle_state,
            enroll_charter_runner=body.enroll_charter_runner,
            body_transformer=_spill_transformer(
                subject=body.subject,
                body=body.body,
                from_agent=body.from_agent,
                allow_long_body=body.allow_long_body,
                holder=spill_holder,
            ),
        )
    except Exception as exc:
        mapped = spill_error_http(exc)
        if mapped is not None:
            status_code, detail = mapped
            raise HTTPException(status_code=status_code, detail=detail) from exc
        if isinstance(exc, (EnrollmentTagError, ThreadClassificationError)):
            _raise_enrollment_denied(exc)
            raise
        if isinstance(exc, UnreadTurnsExist):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=exc.to_detail(),
            ) from exc
        if isinstance(exc, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        raise
    prepared = spill_holder.get("prepared")
    return ThreadWithTurnCreated(
        thread=_thread_detail(thread_row),
        turn=build_turn_created(
            prepared or PreparedBody(body=body.body),
            turn_id=turn_id,
            thread=thread_row["id"],
            turn_number=turn_number,
            created_at=datetime.fromisoformat(ts),
            from_agent=body.from_agent,
            to_agent=body.to,
            subject=body.subject,
        ),
    )
