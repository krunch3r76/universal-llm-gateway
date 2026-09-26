"""Unified send route: continue an existing thread or create-by-new_slug."""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import HTTPException, status
from openapi_mcp.binding import x_mcp

from ...body_auto_spill import build_turn_created, prepare_body_for_insert
from ...checkpoint_auto_stamp_wiring import load_thread_tags
from ...db import SlugExists, create_turn, get_thread, normalize_thread_id
from ...db.turns import UnreadTurnsExist
from ...enrollment_guard import EnrollmentTagError
from ...resume_fence_citation import check_send_citation_gate, release_on_clean_send
from ...thread_classification import ThreadClassificationError
from ...turns_models import TurnSendCreate, TurnSendCreated, slug_exists_detail
from . import router
from .crud import _raise_enrollment_denied
from .detail import _thread_detail
from .new_thread_send import run_new_thread_send
from .send_prep import (
    _maybe_auto_bind_lane_on_send,
    _raise_if_turn_body_over_limit,
    _raise_spill_http,
    _resolve_send_supersedes,
    _validate_lane_bind_pre_mint,
)
from .send_sidecar import _send_with_sidecar


def _send_xor_violation(*, provided: list[str]) -> dict[str, object]:
    if provided:
        message = "thread and new_slug are mutually exclusive — provide exactly one"
    else:
        message = "exactly one of thread or new_slug is required — neither was provided"
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
        return await asyncio.to_thread(_send_with_sidecar, body)
    att_dicts = [a.model_dump() for a in body.attachments] if body.attachments else None

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
        _validate_lane_bind_pre_mint(body)
        _raise_if_turn_body_over_limit(body.body, allow_long_body=body.allow_long_body)
        try:
            thread_row, turn_id, ts, turn_number, prepared = await asyncio.to_thread(
                run_new_thread_send,
                route="send",
                strict_slug=True,
                slug=body.new_slug,
                summary=body.summary,
                tags=body.tags or [],
                lifecycle_state=body.lifecycle_state,
                enroll_charter_runner=body.enroll_charter_runner,
                from_agent=body.from_agent,
                to_agent=body.to,
                subject=body.subject,
                body=body.body,
                turn_status=body.status,
                attachments=att_dicts,
                allow_long_body=body.allow_long_body,
                lane_bind_body=body,
            )
        except (EnrollmentTagError, ThreadClassificationError) as exc:
            _raise_enrollment_denied(exc)
            raise
        except SlugExists as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=slug_exists_detail(
                    slug=exc.slug,
                    existing_thread_id=exc.existing_thread_id,
                ),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        return TurnSendCreated(
            send_path="new_thread",
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
            sidecar_uri=prepared.sidecar_uri,
            sidecar_sha256=prepared.sidecar_sha256,
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

    thread_id = await asyncio.to_thread(normalize_thread_id, body.thread)
    await asyncio.to_thread(
        _maybe_auto_bind_lane_on_send, body=body, thread_id=thread_id
    )
    thread_tags = await asyncio.to_thread(load_thread_tags, thread_id)
    citation_refusal = await asyncio.to_thread(
        check_send_citation_gate,
        thread_id=thread_id,
        from_agent=body.from_agent,
        subject=body.subject,
        body=body.body,
        fence_id=body.fence_id,
    )
    if citation_refusal is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=citation_refusal,
        )
    storage_supersedes, echo_turn_number, echo_turn_id = await asyncio.to_thread(
        _resolve_send_supersedes,
        thread_id=thread_id,
        subject=body.subject,
        thread_tags=thread_tags,
        turn_number=body.supersedes_turn,
        turn_id_alias=body.supersedes_turn_id,
    )
    try:
        prepared = await asyncio.to_thread(
            prepare_body_for_insert,
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
        thread_row, turn_id, ts, turn_number, marked_read = await asyncio.to_thread(
            create_turn,
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
    thread_row = await asyncio.to_thread(get_thread, thread_id) or thread_row
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
    if body.fence_id:
        await asyncio.to_thread(
            release_on_clean_send,
            fence_id=body.fence_id,
            release_turn=turn_number,
        )
    thread_row = await asyncio.to_thread(get_thread, thread_id) or thread_row
    return TurnSendCreated(
        send_path="continue",
        thread=_thread_detail(thread_row),
        turn=turn_created,
        marked_read=marked_read,
        sidecar_uri=prepared.sidecar_uri,
        sidecar_sha256=prepared.sidecar_sha256,
    )
