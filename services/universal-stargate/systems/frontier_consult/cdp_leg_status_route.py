"""Read-only CDP in-flight leg status for bus-side producer witness."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from .cdp_generate_inflight_ledger import read_inflight_leg
from .skill_suggest_durable_state import query_event_service

cdp_leg_status_router = APIRouter(prefix="/api/v1/frontier", tags=["frontier-cdp-leg"])


class CdpLegStatusResponse(BaseModel):
    found: bool
    open: bool | None = None
    terminal: Literal["completed", "failed"] | None = None
    delivered: bool | None = None


def terminal_status_for_execution(
    execution_id: str,
) -> Literal["completed", "failed"] | None:
    """Map proof / stalled events to bus terminal_status (same evidence as finalize)."""
    for signal, status in (
        ("cdp.generate.proof", "completed"),
        ("cdp.generate.stalled", "failed"),
    ):
        result = query_event_service(
            "signal-events",
            {"signal": signal, "execution_id": execution_id, "limit": 5},
        )
        for row in result.get("rows") or []:
            if not isinstance(row, dict):
                continue
            payload = row.get("payload")
            if isinstance(payload, str):
                continue
            if (
                isinstance(payload, dict)
                and payload.get("execution_id") == execution_id
            ) or row.get("execution_id") == execution_id:
                return status  # type: ignore[return-value]
    return None


def build_leg_status_payload(execution_id: str) -> dict[str, Any]:
    """Pure leg read + event lookup for GET handler and offline tests."""
    leg = read_inflight_leg(execution_id)
    if leg is None:
        return {"found": False}
    is_open = not leg.proof_emitted and not leg.abandoned
    terminal = None if is_open else terminal_status_for_execution(execution_id)
    return {
        "found": True,
        "open": is_open,
        "terminal": terminal,
        "delivered": leg.delivered,
    }


@cdp_leg_status_router.get("/cdp-legs/{execution_id}")
def get_cdp_leg_status(execution_id: str) -> CdpLegStatusResponse:
    """Return 200 with ``found:false`` when the ledger has no row (never HTTP 404)."""
    payload = build_leg_status_payload(execution_id)
    return CdpLegStatusResponse(**payload)
