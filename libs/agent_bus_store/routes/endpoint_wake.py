"""Internal wake-ledger routes. Not MCP ops."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from ..db.endpoint_wake_ledger import (
    WakeConflictError,
    advance_scan,
    claim_due_wakes,
    get_cursor,
    record_wake_result,
)

router = APIRouter()


class ScanBody(BaseModel):
    consumer: str
    expected_high_water: int
    new_high_water: int
    pending: list[dict[str, Any]]


class ClaimBody(BaseModel):
    debounce_s: int = 600
    claim_ttl_s: int = 300
    limit: int = 1


class ResultBody(BaseModel):
    wake_id: str
    outcome: str
    reason: str | None = None
    route: str | None = None
    subscription_id: int | None = None
    cse_registration_id: str | None = None
    decided_by_rule: str | None = None


@router.get("/endpoint-wake/cursor")
async def cursor_route(
    consumer: str = Query(default="endpoint-wake-dispatcher"),
) -> dict[str, Any]:
    """Read the consumer cursor, cold-starting at MAX(turns.id)."""
    return get_cursor(consumer)


@router.post("/endpoint-wake/scan")
async def scan_route(body: ScanBody) -> dict[str, Any]:
    """CAS-advance the cursor and insert pending wake rows."""
    try:
        return advance_scan(
            body.consumer,
            body.expected_high_water,
            body.new_high_water,
            body.pending,
        )
    except WakeConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.post("/endpoint-wake/claim")
async def claim_route(body: ClaimBody) -> dict[str, Any]:
    """Claim due wakes. The dispatcher passes limit=1."""
    return claim_due_wakes(body.debounce_s, body.claim_ttl_s, body.limit)


@router.post("/endpoint-wake/result")
async def result_route(body: ResultBody) -> dict[str, Any]:
    """Record a delivery outcome for one wake id."""
    try:
        updated = record_wake_result(
            body.wake_id,
            body.outcome,
            body.reason,
            body.route,
            body.subscription_id,
            body.cse_registration_id,
            decided_by_rule=body.decided_by_rule,
        )
    except WakeConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return {"updated": updated}
