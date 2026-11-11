"""Seat ledger routes — host-side relay (not MCP verbs)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Query, status
from pydantic import BaseModel

from ...db.seat_ledger import (
    SeatLedgerError,
    current_seat,
    list_open_seats,
    seat_drift_summary,
    seat_history,
    transition_seat,
)
from ...db.threads import normalize_thread_id
from . import router


class SeatTransitionBody(BaseModel):
    transition: str
    chat_url: str | None = None
    expected_epoch: int | None = None
    actor: str
    evidence: str | dict[str, Any] | None = None
    request_id: str
    contested: bool = False


@router.post("/threads/{thread_id}/seat")
async def seat_transition_route(
    thread_id: str, body: SeatTransitionBody
) -> dict[str, Any]:
    thread_id = normalize_thread_id(thread_id)
    try:
        return transition_seat(
            thread_id=thread_id,
            transition=body.transition,  # type: ignore[arg-type]
            chat_url=body.chat_url,
            expected_epoch=body.expected_epoch,
            actor=body.actor,
            evidence=body.evidence,
            request_id=body.request_id,
            contested=body.contested,
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        ) from None
    except SeatLedgerError as exc:
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        if exc.code in ("seat_epoch_conflict", "seat_chat_held"):
            status_code = status.HTTP_409_CONFLICT
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": exc.message, **exc.body},
        ) from None


@router.get("/threads/{thread_id}/seat")
async def seat_current_route(
    thread_id: str,
    history_limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    thread_id = normalize_thread_id(thread_id)
    try:
        return {
            "current": current_seat(thread_id=thread_id),
            "history": seat_history(thread_id=thread_id, limit=history_limit),
        }
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        ) from None


@router.get("/seats")
async def seats_list_route(
    state: str | None = Query(None, description="open|closed|contested|all"),
) -> dict[str, Any]:
    seats = list_open_seats(state=state)
    return {"seats": seats, "count": len(seats)}


@router.get("/seats/drift")
async def seats_drift_route() -> dict[str, Any]:
    return seat_drift_summary()
