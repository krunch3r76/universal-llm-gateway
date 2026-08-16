"""Bulk inbox triage route: dry-run preview + confirm-token execute."""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException, status
from openapi_mcp.binding import x_mcp

from ...db import (
    consume_triage_confirm_token,
    execute_triage_close,
    execute_triage_mark_read,
    issue_triage_confirm_token,
    list_triage_candidates,
)
from ...turns_models import (
    TRIAGE_THREAD_CAP,
    ThreadTriageCandidate,
    ThreadTriageDryRun,
    ThreadTriageExecuted,
    ThreadTriageRequest,
    parse_older_than,
    triage_floor_error,
)
from . import router


def _triage_confirm_error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": code,
            "message": message,
            "retryable": code == "confirm_token_expired",
            "source": "agent_bus_store.triage",
        },
    )


def _emit_triage_event(signal: str, payload: dict[str, object]) -> None:
    from ...events.publisher import emit

    emit(signal, payload, role="coordination")


@router.post(
    "/threads/triage",
    response_model=ThreadTriageDryRun | ThreadTriageExecuted,
    openapi_extra=x_mcp("triage", tool="agent_bus"),
)
async def triage_threads_route(body: ThreadTriageRequest) -> ThreadTriageDryRun | ThreadTriageExecuted:
    """Bulk inbox hygiene — preview (dry_run) or execute with confirm_token."""
    if floor := triage_floor_error(body.action, body.older_than):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=floor)

    try:
        activity_cutoff = parse_older_than(body.older_than)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_older_than",
                "message": str(exc),
                "retryable": False,
                "source": "agent_bus_store.triage",
            },
        ) from exc

    all_rows, total_candidates = list_triage_candidates(
        agent=body.from_agent,
        activity_cutoff=activity_cutoff,
        action=body.action,
        status=body.status.value if body.status is not None else None,
    )
    capped = total_candidates > TRIAGE_THREAD_CAP
    preview_rows = all_rows[:TRIAGE_THREAD_CAP]
    candidate_ids = [str(row["id"]) for row in preview_rows]

    if body.dry_run:
        confirm_token, expires_at = issue_triage_confirm_token(
            agent=body.from_agent,
            action=body.action,
            older_than=body.older_than,
            status=body.status.value if body.status is not None else None,
            candidate_ids=candidate_ids,
        )
        _emit_triage_event(
            "mcp.agentbus.triage.dry_run",
            {
                "agent": body.from_agent,
                "filter": {
                    "older_than": body.older_than,
                    "status": body.status.value if body.status else None,
                    "action": body.action,
                },
                "total_candidates": total_candidates,
                "capped": capped,
                "confirm_token_id": confirm_token,
            },
        )
        return ThreadTriageDryRun(
            candidates=[
                ThreadTriageCandidate(
                    id=row["id"],
                    slug=row["slug"],
                    last_activity_at=datetime.fromisoformat(row["last_activity_at"]),
                    unread_count=int(row["unread_count"]),
                )
                for row in preview_rows
            ],
            total_candidates=total_candidates,
            capped=capped,
            confirm_token=confirm_token,
            expires_at=expires_at,
        )

    if not body.confirm_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "confirm_token_required",
                "message": "dry_run=false requires confirm_token from the preview call",
                "retryable": True,
                "source": "agent_bus_store.triage",
            },
        )

    token_status = consume_triage_confirm_token(
        token_id=body.confirm_token,
        agent=body.from_agent,
        action=body.action,
        older_than=body.older_than,
        status=body.status.value if body.status is not None else None,
        candidate_ids=candidate_ids,
    )
    if token_status == "invalid":
        raise _triage_confirm_error(
            "confirm_token_invalid",
            "confirm_token is invalid or already used",
        )
    if token_status == "expired":
        raise _triage_confirm_error(
            "confirm_token_expired",
            "confirm_token expired (10 minute TTL); re-run dry_run",
        )
    if token_status == "filter_mismatch":
        raise _triage_confirm_error(
            "confirm_token_filter_mismatch",
            "confirm_token does not match the current filter or candidate set",
        )

    marked_read = 0
    closed = 0
    if body.action == "mark_read":
        marked_read = execute_triage_mark_read(
            agent=body.from_agent,
            thread_ids=candidate_ids,
        )
    else:
        closed = execute_triage_close(thread_ids=candidate_ids)

    _emit_triage_event(
        "mcp.agentbus.triage.executed",
        {
            "agent": body.from_agent,
            "action": body.action,
            "thread_count": len(candidate_ids),
            "confirm_token_id": body.confirm_token,
            "marked_read": marked_read,
            "closed": closed,
        },
    )
    return ThreadTriageExecuted(
        action=body.action,
        thread_count=len(candidate_ids),
        marked_read=marked_read,
        closed=closed,
        confirm_token_id=body.confirm_token,
    )
