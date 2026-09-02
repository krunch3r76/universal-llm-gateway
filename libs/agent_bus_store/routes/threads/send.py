"""Unified send route: continue an existing thread or create-by-new_slug."""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException, status
from openapi_mcp.binding import x_mcp

from ...body_auto_spill import (
    PreparedBody,
    build_turn_created,
    prepare_body_for_insert,
    spill_error_http,
)
from ...checkpoint_auto_stamp_wiring import load_thread_tags
from ...db import (
    SlugExists,
    create_thread_with_turn,
    create_turn,
    get_thread,
    normalize_thread_id,
)
from ...db.turns import UnreadTurnsExist
from ...enrollment_guard import EnrollmentTagError
from ...thread_classification import ThreadClassificationError
from ...turns_models import TurnSendCreate, TurnSendCreated
from . import router
from .crud import _raise_enrollment_denied
from .detail import _thread_detail
from .send_prep import (
    _maybe_auto_bind_lane_on_send,
    _raise_spill_http,
    _resolve_send_supersedes,
    _spill_transformer,
)
from .send_sidecar import _send_with_sidecar


def _send_xor_violation(*, provided: list[str]) -> dict[str, object]:
    if provided:
        message = (
            "thread and new_slug are mutually exclusive — provide exactly one"
        )
    else:
        message = (
            "exactly one of thread or new_slug is required — neither was provided"
        )
    return {
        "error": message,
        "reason": "send_xor_violation",
        "provided": provided,
        "required": "exactly_one_of_thread_or_new_slug",
    }


@router.post(
    "/threads/send",
    status_code=status.HTTP_201_CREATED,
    response_model=TurnSendCreated,
    openapi_extra=x_mcp("send", tool="agent_bus"),
)
async def send_route(body: TurnSendCreate) -> TurnSendCreated:
    """Unified send: create new thread (new_slug) OR continue existing (thread)."""
    has_new_slug = body.new_slug is not None
    has_thread = bool(body.thread)
    if has_new_slug and has_thread:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_send_xor_violation(provided=["thread", "new_slug"]),
        )
    if not has_new_slug and not has_thread:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_send_xor_violation(provided=[]),
        )
    if body.sidecar_content is not None:
        return _send_with_sidecar(body)
    att_dicts = [a.model_dump() for a in body.attachments] if body.attachments else None
    spill_holder: dict[str, PreparedBody] = {}

    if has_new_slug:
        if body.after_turn is not None and body.after_turn > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "after_turn > 0 is invalid on the new_slug (new-thread) path",
                    "reason": "after_turn_not_valid_on_new_thread",
                },
            )
        if body.supersedes_turn is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": (
                        "supersedes_turn is only valid on the continue (thread=) path"
                    ),
                    "reason": "supersedes_turn_not_valid_on_new_thread",
                },
            )
        try:
            thread_row, turn_id, ts, turn_number = create_thread_with_turn(
                slug=body.new_slug,
                summary=body.summary,
                from_agent=body.from_agent,
                to_agent=body.to,
                subject=body.subject,
                body=body.body,
                status=body.status,
                after_turn=0,
                attachments=att_dicts,
                tags=body.tags or [],
                lifecycle_state=body.lifecycle_state,
                strict_slug=True,
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
            if isinstance(exc, SlugExists):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "slug_exists",
                        "slug": exc.slug,
                        "existing_thread_id": exc.existing_thread_id,
                        "message": (
                            f"A thread with slug {exc.slug!r} already exists "
                            f"(thread {exc.existing_thread_id}). "
                            "Use send(thread=<id>, ...) to continue it or choose "
                            "a different new_slug."
                        ),
                    },
                ) from exc
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
        _maybe_auto_bind_lane_on_send(body=body, thread_id=thread_row["id"])
        thread_row = get_thread(thread_row["id"]) or thread_row
        return TurnSendCreated(
            send_path="new_thread",
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
            sidecar_uri=prepared.sidecar_uri if prepared else None,
            sidecar_sha256=prepared.sidecar_sha256 if prepared else None,
        )

    if body.lifecycle_state is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": (
                    "lifecycle_state is only valid on the new_slug (new-thread) path"
                ),
                "reason": "lifecycle_state_not_valid_on_continue",
            },
        )

    thread_id = normalize_thread_id(body.thread)
    thread_tags = load_thread_tags(thread_id)
    storage_supersedes, echo_turn_number, echo_turn_id = _resolve_send_supersedes(
        thread_id=thread_id,
        subject=body.subject,
        thread_tags=thread_tags,
        turn_number=body.supersedes_turn,
        turn_id_alias=body.supersedes_turn_id,
    )
    try:
        prepared = prepare_body_for_insert(
            thread=thread_id,
            subject=body.subject,
            body=body.body,
            from_agent=body.from_agent,
            allow_long_body=body.allow_long_body,
            thread_tags=thread_tags,
            supersedes_turn=echo_turn_number,
        )
    except Exception as exc:
        _raise_spill_http(exc, thread_id=thread_id)
        raise  # pragma: no cover — _raise_spill_http always raises
    try:
        thread_row, turn_id, ts, turn_number, marked_read = create_turn(
            thread_id=thread_id,
            from_agent=body.from_agent,
            to_agent=body.to,
            subject=body.subject,
            body=prepared.body,
            status=body.status,
            after_turn=body.after_turn,
            supersedes_turn=storage_supersedes,
            attachments=att_dicts,
            close=body.close,
            mark_read=body.mark_read,
        )
    except UnreadTurnsExist as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=e.to_detail(),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": str(exc), "reason": "supersedes_turn_invalid"},
        ) from exc
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    thread_row = get_thread(thread_id) or thread_row
    turn_created = build_turn_created(
        prepared,
        turn_id=turn_id,
        thread=thread_id,
        turn_number=turn_number,
        created_at=datetime.fromisoformat(ts),
        from_agent=body.from_agent,
        to_agent=body.to,
        subject=body.subject,
        superseded_turn_number=echo_turn_number,
        superseded_turn_id=echo_turn_id,
        thread_tags=thread_tags,
        supersedes_turn=echo_turn_number,
    )
    thread_row = get_thread(thread_id) or thread_row
    return TurnSendCreated(
        send_path="continue",
        thread=_thread_detail(thread_row),
        turn=turn_created,
        marked_read=marked_read,
        sidecar_uri=prepared.sidecar_uri,
        sidecar_sha256=prepared.sidecar_sha256,
    )
