"""Shared sync sequence for new-thread send and with-turn routes."""

from __future__ import annotations

import time
from typing import Any, Literal

from cortex_store.dispatch_ops._thread_sidecar import SidecarContentTooLargeError
from fastapi import HTTPException, status

from ...body_auto_spill import (
    BodyTooLargeError,
    PreparedBody,
    prepare_body_for_insert,
    spill_error_http,
)
from ...checkpoint_projection import CheckpointBodyTooLargeError, is_checkpoint_subject
from ...db import get_thread
from ...db.thread_mint import mint_thread
from ...db.turns import insert_turn
from ...events.lifecycle import emit_sidecar_orphaned
from ...events.send_hold import emit_send_hold_measured
from ...turns_models import TurnSendCreate
from .send_prep import _bind_lane_on_send, _raise_post_mint_http

RouteKind = Literal["send", "with_turn"]


def _map_prepare_failure(
    exc: BaseException,
    *,
    thread_id: str,
) -> None:
    mapped = spill_error_http(exc, thread_id=thread_id)
    if mapped is not None:
        status_code, detail = mapped
        reason = (
            "checkpoint_body_too_large"
            if isinstance(exc, CheckpointBodyTooLargeError)
            else "body_too_large"
            if isinstance(exc, (BodyTooLargeError, SidecarContentTooLargeError))
            else "sidecar_write_failed"
        )
        _raise_post_mint_http(
            thread_id=thread_id,
            status_code=status_code,
            detail=detail,
            orphan_reason=reason,
        )
    if isinstance(exc, HTTPException):
        detail_obj: dict[str, object]
        if isinstance(exc.detail, dict):
            detail_obj = exc.detail
        else:
            detail_obj = {"error": str(exc.detail)}
        _raise_post_mint_http(
            thread_id=thread_id,
            status_code=exc.status_code,
            detail=detail_obj,
            orphan_reason="body_prepare_failed",
        )
    _raise_post_mint_http(
        thread_id=thread_id,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "body_prepare_failed",
            "message": str(exc),
            "source": "agent_bus_store.send",
            "retryable": False,
            "data": {"thread_id": thread_id},
        },
        orphan_reason="body_prepare_failed",
    )


def run_new_thread_send(
    *,
    route: RouteKind,
    strict_slug: bool,
    slug: str,
    summary: str | None,
    tags: list[str] | None,
    lifecycle_state: str | None,
    enroll_charter_runner: bool,
    from_agent: str,
    to_agent: str,
    subject: str,
    body: str,
    turn_status: str,
    attachments: list[dict[str, Any]] | None,
    allow_long_body: bool,
    lane_bind_body: TurnSendCreate | None = None,
) -> tuple[dict[str, Any], int, str, int, PreparedBody]:
    """Mint, optional lane bind, prepare outside any transaction, then insert turn."""
    thread_row, mint_hold_ms = mint_thread(
        slug=slug,
        summary=summary,
        tags=tags,
        lifecycle_state=lifecycle_state,
        enroll_charter_runner=enroll_charter_runner,
        strict_slug=strict_slug,
    )
    thread_id = thread_row["id"]

    if lane_bind_body is not None:
        _bind_lane_on_send(body=lane_bind_body, thread_id=thread_id)

    body_chars = len(body)
    t_prepare = time.monotonic()
    try:
        prepared = prepare_body_for_insert(
            thread=thread_id,
            subject=subject,
            body=body,
            from_agent=from_agent,
            allow_long_body=allow_long_body,
        )
    except Exception as exc:
        _map_prepare_failure(exc, thread_id=thread_id)
    prepare_ms = (time.monotonic() - t_prepare) * 1000.0

    t_insert = time.monotonic()
    try:
        turn_id, ts, turn_number = insert_turn(
            thread=thread_id,
            from_agent=from_agent,
            to_agent=to_agent,
            subject=subject,
            body=prepared.body,
            status=turn_status,
            after_turn=None,
            supersedes_turn=None,
            attachments=attachments,
        )
    except Exception as exc:
        if prepared.sidecar_uri:
            emit_sidecar_orphaned(
                uri=prepared.sidecar_uri,
                error=str(exc),
                thread_id=thread_id,
            )
        detail = {
            "code": "turn_insert_failed",
            "reason": "turn_insert_failed",
            "message": "Turn insert failed after thread mint; thread may be orphaned.",
            "retryable": True,
            "source": "agent_bus_store.send",
            "data": {
                "thread_id": thread_id,
                **(
                    {"sidecar_uri": prepared.sidecar_uri}
                    if prepared.sidecar_uri
                    else {}
                ),
            },
        }
        _raise_post_mint_http(
            thread_id=thread_id,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail,
            orphan_reason="turn_insert_failed",
        )
    insert_call_ms = (time.monotonic() - t_insert) * 1000.0

    emit_send_hold_measured(
        thread=thread_id,
        route=route,  # type: ignore[arg-type]
        is_checkpoint=is_checkpoint_subject(subject),
        body_chars=body_chars,
        spilled=prepared.sidecar_uri is not None,
        mint_hold_ms=mint_hold_ms,
        prepare_ms=prepare_ms,
        insert_call_ms=insert_call_ms,
    )

    thread_row = get_thread(thread_id) or thread_row
    return thread_row, turn_id, ts, turn_number, prepared
