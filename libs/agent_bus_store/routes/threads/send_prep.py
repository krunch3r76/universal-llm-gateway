"""Shared pre-write helpers for agent-bus send, turn, and dispatch routes.

Callers are send, with-turn, sidecar, POST /turns, PATCH body/append, and
dispatch claim-and-post / admit-and-post. The unexpanded ``$file:`` refusal
lives here so those routes share one predicate and one 422 envelope.
"""

from __future__ import annotations

from agent_seat.registry import normalize_bus_address
from fastapi import HTTPException, status

from ...body_auto_spill import spill_error_http
from ...db import get_thread, normalize_thread_id
from ...db.lane_associations import (
    associate_lane,
    invalid_lane_role_envelope,
    lane_bind_incomplete_envelope,
)
from ...events.lifecycle import emit_thread_orphaned
from ...lane_roles import parse_lane_role
from ...supersedes_turn_boundary import (
    SupersedesTurnNotFoundError,
    resolve_send_supersedes,
)
from ...thread_classification import liaison_tag_on_child_without_root
from ...turns_models import (
    TurnSendCreate,
    body_unexpanded_file_token_envelope,
    post_mint_detail,
    text_is_unexpanded_file_token,
    turn_body_limit_error,
)

MAESTRO_MILESTONE_PREFIXES = frozenset(
    {
        "PLAN",
        "VERDICT",
        "LANDED",
        "DONE",
        "FAILED",
        "BLOCKED",
        "QUESTION",
        "RULING",
        "ACK",
        "AMEND",
        "SEAT_REGISTRATION",
        "DISPOSITION",
        "DIRECTIVE",
        "ORIENTED",
        "ROUTE",
        "HOLD",
        "COMPLETE",
    }
)
MAESTRO_MILESTONE_BODY_MAX_CHARS = 2000
MAESTRO_REROUTE_TO_HOUSE_PREFIXES = frozenset({"PICKUP", "FACT"})
_MAESTRO_COMPOSE_TARGETS = frozenset({"maestro", "web-anthropic"})


def maestro_subject_head(subject: str | None) -> str:
    """Subject prefix token before ``|``, first word uppercased (``PLAN r2`` → ``PLAN``)."""
    head = (subject or "").split("|", 1)[0].strip().upper()
    if not head:
        return ""
    return head.split()[0]


def is_maestro_milestone_turn(*, to: str, subject: str | None) -> bool:
    """True for maestro/web milestone heads (L17/L29b), including ``IN FLIGHT|``."""
    if normalize_bus_address(to) not in _MAESTRO_COMPOSE_TARGETS:
        return False
    head_part = (subject or "").split("|", 1)[0].strip().upper()
    if head_part.startswith("IN FLIGHT"):
        return True
    token = maestro_subject_head(subject)
    return token in MAESTRO_MILESTONE_PREFIXES


def milestone_body_over_limit_envelope() -> dict[str, str]:
    return {
        "code": "milestone_body_over_2000_chars",
        "error": "Maestro milestone body exceeds 2000 characters without sidecar_content",
        "fix_hint": (
            "Move long detail to sidecar_content and keep body <= 2000 characters."
        ),
    }


def _raise_if_maestro_milestone_body_over_limit(
    body: TurnSendCreate,
) -> None:
    if body.allow_long_body:
        return
    if body.sidecar_content is not None:
        return
    if not is_maestro_milestone_turn(to=body.to or "", subject=body.subject):
        return
    if len(body.body) <= MAESTRO_MILESTONE_BODY_MAX_CHARS:
        return
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=milestone_body_over_limit_envelope(),
    )


def apply_maestro_milestone_reroute(
    *, to: str, from_agent: str, subject: str | None
) -> str:
    """Rewrite ``to=maestro`` / ``web-anthropic`` ``PICKUP``/``FACT`` to the composing house."""
    if not (from_agent or "").strip():
        return to
    if normalize_bus_address(to) not in _MAESTRO_COMPOSE_TARGETS:
        return to
    if maestro_subject_head(subject) in MAESTRO_REROUTE_TO_HOUSE_PREFIXES:
        return from_agent
    return to


def apply_maestro_compose_reroute(body: TurnSendCreate) -> None:
    """In-place compose-site reroute for maestro milestone policy (L17/L21)."""
    body.to = apply_maestro_milestone_reroute(
        to=body.to or "",
        from_agent=body.from_agent or "",
        subject=body.subject,
    )


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


def _validate_lane_bind_pre_mint(body: TurnSendCreate) -> None:
    """Pure lane gates — no ``thread_id`` required; run before thread mint."""
    has_parent = body.parent_thread is not None
    has_role = body.lane_role is not None
    if not has_parent and not has_role:
        return
    if not has_parent or not has_role:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=lane_bind_incomplete_envelope(
                provided=[
                    k
                    for k, v in (
                        ("parent_thread", has_parent),
                        ("lane_role", has_role),
                    )
                    if v
                ]
            ),
        )
    try:
        parse_lane_role(body.lane_role or "")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=invalid_lane_role_envelope(
                lane_role=body.lane_role or "",
                reason=str(exc),
            ),
        ) from exc
    parent_id = normalize_thread_id(body.parent_thread)
    if get_thread(parent_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {parent_id} not found",
        )
    if liaison_tag_on_child_without_root(body.tags):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "liaison_tag_on_child_lane",
                "detail": (
                    "lane:liaison belongs on a liaison house (with role:root), "
                    "not on a child lane."
                ),
            },
        )


def _raise_if_unexpanded_file_token(text: str | None) -> None:
    """Refuse a whole-value ``$file:`` token before mint, insert, or sidecar write.

    Breaks when the nested token is stored (new thread, continue, with-turn,
    sidecar_content, POST /turns, PATCH body or append, dispatch
    claim-and-post, or dispatch admit-and-post). Mid-text mentions are left
    alone. ``text is None`` is a no-op so optional patch fields can pass.
    """
    if text is None or not text_is_unexpanded_file_token(text):
        return
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=body_unexpanded_file_token_envelope(),
    )


def _raise_if_turn_body_over_limit(body: str, *, allow_long_body: bool = False) -> None:
    if error_detail := turn_body_limit_error(body, allow_long_body=allow_long_body):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=error_detail,
        )


def _raise_post_mint_http(
    *,
    thread_id: str,
    status_code: int,
    detail: dict[str, object],
    orphan_reason: str,
) -> None:
    """Refusal after thread mint — name the created row and emit orphan signal."""
    error_key = detail.get("code") or detail.get("reason") or detail.get("error")
    emit_thread_orphaned(
        thread_id=thread_id,
        reason=orphan_reason,
        error=str(error_key) if error_key is not None else None,
    )
    enriched = post_mint_detail(detail, thread_id=thread_id)
    enriched["orphan_reason"] = orphan_reason
    raise HTTPException(
        status_code=status_code,
        detail=enriched,
    )


def _bind_lane_on_send(*, body: TurnSendCreate, thread_id: str) -> None:
    """Associate lane when both parent_thread and lane_role are set."""
    if body.parent_thread is None and body.lane_role is None:
        return
    try:
        associate_lane(
            thread_id=thread_id,
            parent_thread_id=body.parent_thread,
            lane_role=body.lane_role,
        )
    except LookupError as exc:
        _raise_post_mint_http(
            thread_id=thread_id,
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": str(exc), "reason": "lane_bind_parent_not_found"},
            orphan_reason="lane_bind_failed",
        )
    except ValueError as exc:
        if "lane_role" in str(exc):
            _raise_post_mint_http(
                thread_id=thread_id,
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=invalid_lane_role_envelope(
                    lane_role=body.lane_role or "",
                    reason=str(exc),
                ),
                orphan_reason="lane_bind_failed",
            )
        _raise_post_mint_http(
            thread_id=thread_id,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": str(exc), "reason": "invalid_lane_bind"},
            orphan_reason="lane_bind_failed",
        )


def _maybe_auto_bind_lane_on_send(*, body: TurnSendCreate, thread_id: str) -> None:
    """Validate lane fields then bind — for continue path or post-mint new_slug."""
    _validate_lane_bind_pre_mint(body)
    _bind_lane_on_send(body=body, thread_id=thread_id)
