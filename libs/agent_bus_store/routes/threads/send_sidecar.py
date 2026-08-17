"""E4 send path: thread id -> sidecar write -> turn insert."""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException, status

from ...body_auto_spill import PreparedBody, build_turn_created
from ...checkpoint_auto_stamp_wiring import load_thread_tags
from ...checkpoint_projection import CheckpointBodyTooLargeError
from ...checkpoint_projection_wiring import maybe_project_checkpoint_body
from ...db import close_thread, create_thread, get_thread, normalize_thread_id
from ...enrollment_guard import EnrollmentTagError
from ...thread_classification import ThreadClassificationError
from ...turns_models import (
    TurnSendCreate,
    TurnSendCreated,
    sidecar_content_limit_error,
    sidecar_write_failed_envelope,
    turn_body_limit_error,
)
from .crud import _raise_enrollment_denied
from .detail import _thread_detail
from .send_prep import _maybe_auto_bind_lane_on_send, _resolve_send_supersedes


def _send_with_sidecar(body: TurnSendCreate) -> TurnSendCreated:
    """E4 send path: thread id → sidecar write → turn insert."""
    from cortex_store.dispatch_ops._thread_sidecar import (
        SidecarWriteError,
        append_sidecar_pointer_line,
        write_thread_sidecar_for_send,
    )

    from ...db.connection import connect
    from ...db.turns import UnreadTurnsExist, insert_turn, mark_sender_unread_in_thread
    from ...events.lifecycle import emit_sidecar_orphaned, emit_sidecar_written

    if error_detail := sidecar_content_limit_error(body.sidecar_content or ""):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=error_detail,
        )

    has_new_slug = body.new_slug is not None
    att_dicts = [a.model_dump() for a in body.attachments] if body.attachments else None
    thread_id: str | None = None
    send_path: str

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
        with connect() as conn:
            existing = conn.execute(
                "SELECT id FROM threads WHERE slug = ? LIMIT 1",
                (body.new_slug,),
            ).fetchone()
            if existing is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "slug_exists",
                        "slug": body.new_slug,
                        "existing_thread_id": existing["id"],
                        "message": (
                            f"A thread with slug {body.new_slug!r} already exists "
                            f"(thread {existing['id']}). "
                            "Use send(thread=<id>, ...) to continue it or choose "
                            "a different new_slug."
                        ),
                    },
                )
        try:
            thread_row = create_thread(
                thread_id=None,
                slug=body.new_slug,
                summary=body.summary,
                tags=body.tags or [],
                lifecycle_state=body.lifecycle_state,
                enroll_charter_runner=body.enroll_charter_runner,
            )
        except (EnrollmentTagError, ThreadClassificationError) as exc:
            _raise_enrollment_denied(exc)
            raise
        if thread_row is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create thread for sidecar send",
            )
        thread_id = thread_row["id"]
        send_path = "new_thread"
        _maybe_auto_bind_lane_on_send(body=body, thread_id=thread_id)
    else:
        thread_id = normalize_thread_id(body.thread)
        if get_thread(thread_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Thread {thread_id} not found",
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
        send_path = "continue"

    assert thread_id is not None
    try:
        sidecar = write_thread_sidecar_for_send(
            thread=thread_id,
            subject=body.subject,
            content=body.sidecar_content or "",
            from_agent=body.from_agent,
            sidecar_slug=body.sidecar_slug,
        )
    except SidecarWriteError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=sidecar_write_failed_envelope(
                thread_id=thread_id,
                error=str(exc),
            ),
        ) from exc

    try:
        turn_body = maybe_project_checkpoint_body(
            thread=thread_id,
            subject=body.subject,
            body=body.body,
        )
    except CheckpointBodyTooLargeError as exc:
        raise HTTPException(status_code=413, detail=exc.envelope) from exc

    final_body = append_sidecar_pointer_line(turn_body, sidecar_uri=sidecar.uri)
    if error_detail := turn_body_limit_error(
        final_body,
        allow_long_body=body.allow_long_body,
    ):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=error_detail,
        )

    effective_after = body.after_turn if body.after_turn and body.after_turn > 0 else None
    thread_tags = load_thread_tags(thread_id)
    storage_supersedes, echo_turn_number, echo_turn_id = _resolve_send_supersedes(
        thread_id=thread_id,
        turn_number=body.supersedes_turn,
        turn_id_alias=body.supersedes_turn_id,
    )
    try:
        turn_id, ts, turn_number = insert_turn(
            thread=thread_id,
            from_agent=body.from_agent,
            to_agent=body.to,
            subject=body.subject,
            body=final_body,
            status=body.status,
            after_turn=effective_after,
            supersedes_turn=storage_supersedes,
            attachments=att_dicts,
        )
    except UnreadTurnsExist as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.to_detail(),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": str(exc), "reason": "supersedes_turn_invalid"},
        ) from exc
    except Exception as exc:
        emit_sidecar_orphaned(
            uri=sidecar.uri,
            error=str(exc),
            thread_id=thread_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "turn_insert_failed",
                "reason": "turn_insert_failed",
                "message": "Turn insert failed after sidecar write; sidecar file may be orphaned.",
                "retryable": True,
                "source": "agent_bus_store.send",
                "data": {"thread_id": thread_id, "sidecar_uri": sidecar.uri},
            },
        ) from exc

    marked_read = 0
    if body.mark_read:
        through = effective_after if effective_after else turn_number - 1
        marked_read = mark_sender_unread_in_thread(
            thread=thread_id,
            from_agent=body.from_agent,
            through_turn=through,
        )
    if body.close:
        close_thread(thread_id, mark_all_read=True)

    emit_sidecar_written(
        thread=thread_id,
        turn_number=turn_number,
        uri=sidecar.uri,
        sha256=sidecar.sha256,
        bytes_written=sidecar.body_chars,
    )

    turn_created = build_turn_created(
        PreparedBody(
            body=final_body,
            sidecar_uri=sidecar.uri,
            sidecar_sha256=sidecar.sha256,
        ),
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
        supersedes_turn=body.supersedes_turn or body.supersedes_turn_id,
    )
    thread_row = get_thread(thread_id)
    if thread_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return TurnSendCreated(
        send_path=send_path,  # type: ignore[arg-type]
        thread=_thread_detail(thread_row),
        turn=turn_created,
        marked_read=marked_read,
        sidecar_uri=sidecar.uri,
        sidecar_sha256=sidecar.sha256,
    )
