"""Shared pre-processing helpers for the send + with-turn + sidecar flows."""

from __future__ import annotations

from fastapi import HTTPException, status

from ...body_auto_spill import PreparedBody, prepare_body_for_insert, spill_error_http
from ...db.lane_associations import (
    associate_lane,
    invalid_lane_role_envelope,
    lane_bind_incomplete_envelope,
)
from ...supersedes_turn_boundary import (
    SupersedesTurnNotFoundError,
    resolve_send_supersedes,
)
from ...turns_models import TurnSendCreate


def _spill_transformer(
    *,
    subject: str,
    body: str,
    from_agent: str,
    allow_long_body: bool,
    holder: dict[str, PreparedBody],
):
    """Build a create_thread_with_turn body_transformer that soft-spills."""

    def _transform(thread_id: str) -> str:
        prepared = prepare_body_for_insert(
            thread=thread_id,
            subject=subject,
            body=body,
            from_agent=from_agent,
            allow_long_body=allow_long_body,
        )
        holder["prepared"] = prepared
        return prepared.body

    return _transform


def _raise_spill_http(exc: BaseException, *, thread_id: str | None = None) -> None:
    mapped = spill_error_http(exc, thread_id=thread_id)
    if mapped is None:
        raise exc
    status_code, detail = mapped
    raise HTTPException(status_code=status_code, detail=detail) from exc


def _resolve_send_supersedes(
    *,
    thread_id: str,
    subject: str,
    thread_tags: list[str],
    turn_number: int | None,
    turn_id_alias: int | None,
) -> tuple[int | None, int | None, int | None]:
    """Return (storage_row_id, echo_turn_number, echo_turn_id) for send/supersede."""
    try:
        resolved = resolve_send_supersedes(
            thread=thread_id,
            subject=subject,
            thread_tags=thread_tags,
            turn_number=turn_number,
            turn_id_alias=turn_id_alias,
        )
    except SupersedesTurnNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.to_http_detail(),
        ) from exc
    if resolved is None:
        return None, None, None
    return resolved.turn_id, resolved.turn_number, resolved.turn_id


def _maybe_auto_bind_lane_on_send(*, body: TurnSendCreate, thread_id: str) -> None:
    """Auto-bind a freshly minted lane when both parent_thread and lane_role are set."""
    has_parent = body.parent_thread is not None
    has_role = body.lane_role is not None
    if not has_parent and not has_role:
        return
    if not has_parent or not has_role:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=lane_bind_incomplete_envelope(
                provided=[k for k, v in (("parent_thread", has_parent), ("lane_role", has_role)) if v]
            ),
        )
    try:
        associate_lane(
            thread_id=thread_id,
            parent_thread_id=body.parent_thread,
            lane_role=body.lane_role,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        if "lane_role" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=invalid_lane_role_envelope(
                    lane_role=body.lane_role or "",
                    reason=str(exc),
                ),
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": str(exc), "reason": "invalid_lane_bind"},
        ) from exc
