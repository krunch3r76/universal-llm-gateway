"""Admin active-work endpoint — aggregate in-flight work for drain-aware restart.

A single read-only probe consulted by the manage drain gate before stopping or
restarting Stargate. Aggregates two independent in-flight sources:

- async pipeline dispatches still ``running`` in the dispatch tracker
- requests in flight to gateways (sync chat/pipeline runs, inference)

Invariant: ∀ error response: ``{"error": {"code", "message"}}`` — the canonical
``/api/v1/*`` envelope (¬ ``HTTPException(detail=...)``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from src.core.gateway.in_flight_requests import in_flight_tracker

from ...dependencies import get_auth_dependency, get_proxy

if TYPE_CHECKING:
    from ...stargate_core import StargateProxy

router = APIRouter(tags=["admin"])


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
    total = async_running + requests_in_flight

    return JSONResponse(
        status_code=200,
        content={
            "async_pipelines_running": async_running,
            "requests_in_flight": requests_in_flight,
            "total": total,
            "busy": total > 0,
        },
    )
