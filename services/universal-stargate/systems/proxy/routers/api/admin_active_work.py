"""Admin active-work endpoint — aggregate in-flight work for drain-aware restart.

A single read-only probe consulted by the manage drain gate before stopping or
restarting Stargate. Aggregates two independent in-flight sources:

- async pipeline dispatches still ``running`` in the dispatch tracker
- requests in flight to gateways (sync chat/pipeline runs, inference)

Invariant: ∀ error response: ``{"error": {"code", "message"}}`` — the canonical
``/api/v1/*`` envelope (¬ ``HTTPException(detail=...)``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from claude_bundles.operator_proxy_mission import OPERATOR_PROXY_MISSION_PURPOSES
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from src.core.gateway.in_flight_requests import in_flight_tracker

from ...dependencies import get_auth_dependency, get_proxy

if TYPE_CHECKING:
    from ...stargate_core import StargateProxy

router = APIRouter(tags=["admin"])


def _operator_mission_purpose(purpose: str | None) -> bool:
    return (purpose or "").strip() in OPERATOR_PROXY_MISSION_PURPOSES


def cdp_open_leg_split(
    legs: list[Any],
) -> tuple[int, list[dict[str, str]]]:
    """Split open inflight legs into drain-busy count and named orphans.

    Non-operator purposes count toward ``busy``. Operator-proxy and mission
    legs are listed by execution id and purpose and do not hold the drain.
    """
    non_operator = 0
    orphans: list[dict[str, str]] = []
    for leg in legs:
        purpose = getattr(leg, "purpose", None)
        execution_id = str(getattr(leg, "execution_id", "") or "")
        if _operator_mission_purpose(purpose if isinstance(purpose, str) else None):
            orphans.append(
                {
                    "execution_id": execution_id,
                    "purpose": str(purpose).strip(),
                }
            )
        else:
            non_operator += 1
    return non_operator, orphans


@router.get("/admin/active-work")
async def get_active_work(
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Return aggregate in-flight work counts for drain-aware restart.

    Shape: ``{async_pipelines_running, requests_in_flight, total, busy}``.
    ``busy`` is the single field the drain gate reads; the counts are for
    observability and operator inspection.
    """
    tracker = getattr(proxy, "pipeline_dispatch_tracker", None)
    async_running = 0
    if tracker is not None:
        async_running = sum(
            1 for record in tracker.records.values() if record.status == "running"
        )

    requests_in_flight = in_flight_tracker.get_total_in_flight()
    cdp_non_operator_open = 0
    cdp_legs_orphaned: list[dict[str, str]] = []
    cdp_legs_error: str | None = None
    try:
        from systems.frontier_consult.cdp_generate_inflight_ledger import (
            list_open_inflight_legs,
        )

        cdp_non_operator_open, cdp_legs_orphaned = cdp_open_leg_split(
            list_open_inflight_legs()
        )
    except Exception as exc:  # noqa: BLE001 — ledger miss must not look idle
        cdp_legs_error = type(exc).__name__
        cdp_non_operator_open = 1

    total = async_running + requests_in_flight + cdp_non_operator_open

    content: dict[str, object] = {
        "async_pipelines_running": async_running,
        "requests_in_flight": requests_in_flight,
        "cdp_non_operator_open": cdp_non_operator_open,
        "cdp_legs_orphaned": cdp_legs_orphaned,
        "total": total,
        "busy": total > 0,
    }
    if cdp_legs_error is not None:
        content["cdp_legs_error"] = cdp_legs_error
    return JSONResponse(status_code=200, content=content)
