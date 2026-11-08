"""Internal wake-ledger routes. Not MCP ops."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from openapi_mcp.binding import x_mcp
from pydantic import BaseModel

from ..db.endpoint_wake_ledger import (
    WakeConflictError,
    advance_scan,
    claim_due_wakes,
    get_cursor,
    list_wake_ledger_rows,
    recent_delivered_agent_ids,
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
    wake_detail: dict[str, Any] | None = None


@router.get("/endpoint-wake/cursor")
async def cursor_route(
    consumer: str = Query(default="endpoint-wake-dispatcher"),
) -> dict[str, Any]:
    """Read the consumer cursor, cold-starting at MAX(turns.id)."""
    return get_cursor(consumer)


@router.get(
    "/endpoint-wake/ledger",
    openapi_extra=x_mcp("wake_ledger", tool="agent_bus_read"),
)
async def wake_ledger_route(
    thread: str | None = None,
    turn_id: int | None = None,
    turn_number: int | None = None,
    agent: str | None = None,
    state: str | None = None,
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    """Read wake ledger rows (read-only; same DB as the dispatcher)."""
    rows = list_wake_ledger_rows(
        thread=thread,
        turn_id=turn_id,
        turn_number=turn_number,
        agent=agent,
        state=state,
        limit=limit,
    )
    return {"rows": rows, "count": len(rows)}


@router.get("/endpoint-wake/recent-deliveries")
async def recent_deliveries_route(
    thread: str = Query(...),
    since_s: int = Query(..., ge=1, le=86400),
    before: str | None = Query(
        None,
        description="Anchor stamp (UTC); window ends here instead of now",
    ),
) -> dict[str, Any]:
    """Distinct delivered tab ids on a thread within a recency window (scan helper)."""
    ids = recent_delivered_agent_ids(thread, since_s, before=before)
    return {"delivery_agent_ids": ids, "count": len(ids)}


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
            wake_detail=body.wake_detail,
        )
    except WakeConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return {"updated": updated}
