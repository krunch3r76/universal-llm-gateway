"""Admin active-work endpoint — aggregate in-flight work for drain-aware restart.

A single read-only probe consulted by the manage drain gate before stopping or
restarting Stargate. Aggregates two independent in-flight sources:

- async pipeline dispatches still ``running`` in the dispatch tracker
- requests in flight to gateways (sync chat/pipeline runs, inference)

Invariant: ∀ error response: ``{"error": {"code", "message"}}`` — the canonical
``/api/v1/*`` envelope (¬ ``HTTPException(detail=...)``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from claude_bundles.operator_proxy_mission import OPERATOR_PROXY_MISSION_PURPOSES
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from src.core.gateway.in_flight_requests import in_flight_tracker
from systems.frontier_consult.cdp_generate_reconcile import max_open_leg_s

from ...dependencies import get_auth_dependency, get_proxy

if TYPE_CHECKING:
    from ...stargate_core import StargateProxy

router = APIRouter(tags=["admin"])

# Cap observability payload size when many stale open legs remain in the ledger.
CDP_LEGS_PAST_HORIZON_LIST_MAX = 64


def _operator_mission_purpose(purpose: str | None) -> bool:
    return (purpose or "").strip() in OPERATOR_PROXY_MISSION_PURPOSES


def _cdp_leg_open_within_horizon(leg: Any) -> bool:
    """True while open seconds are strictly less than ``max_open_leg_s``."""
    try:
        admitted = datetime.fromisoformat(leg.admitted_at).timestamp()
    except (TypeError, ValueError, AttributeError):
        return True
    open_s = max(0.0, datetime.now(UTC).timestamp() - admitted)
    max_wall_s = float(getattr(leg, "max_wall_s", 0.0) or 0.0)
    return open_s < max_open_leg_s(max_wall_s)


def cdp_open_leg_split(
    legs: list[Any],
) -> tuple[int, list[dict[str, str]], list[dict[str, str]], int]:
    """Split open inflight legs into drain-busy count, orphans, and past horizon.

    Non-operator purposes within the open-leg horizon count toward ``busy``.
    Operator-proxy and mission legs within the horizon are listed as orphans and
    do not hold the drain. Legs at or past ``max_open_leg_s`` are reported in
    ``cdp_legs_past_horizon`` only.
    """
    non_operator = 0
    orphans: list[dict[str, str]] = []
    past_horizon_all: list[dict[str, str]] = []
    for leg in legs:
        purpose_raw = getattr(leg, "purpose", None)
        purpose = purpose_raw if isinstance(purpose_raw, str) else None
        execution_id = str(getattr(leg, "execution_id", "") or "")
        admitted_at = str(getattr(leg, "admitted_at", "") or "")
        if not _cdp_leg_open_within_horizon(leg):
            past_horizon_all.append(
                {
                    "execution_id": execution_id,
                    "purpose": str(purpose or "").strip(),
                    "admitted_at": admitted_at,
                }
            )
            continue
        if _operator_mission_purpose(purpose):
            orphans.append(
                {
                    "execution_id": execution_id,
                    "purpose": str(purpose).strip(),
                }
            )
        else:
            non_operator += 1
    past_count = len(past_horizon_all)
    past_bounded = past_horizon_all[:CDP_LEGS_PAST_HORIZON_LIST_MAX]
    return non_operator, orphans, past_bounded, past_count


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
    cdp_legs_past_horizon: list[dict[str, str]] = []
    cdp_legs_past_horizon_count = 0
    cdp_legs_error: str | None = None
    try:
        from systems.frontier_consult.cdp_generate_inflight_ledger import (
            list_open_inflight_legs,
        )

        (
            cdp_non_operator_open,
            cdp_legs_orphaned,
            cdp_legs_past_horizon,
            cdp_legs_past_horizon_count,
        ) = cdp_open_leg_split(list_open_inflight_legs())
    except Exception as exc:  # noqa: BLE001 — ledger miss must not look idle
        cdp_legs_error = type(exc).__name__
        cdp_non_operator_open = 1

    total = async_running + requests_in_flight + cdp_non_operator_open

    content: dict[str, object] = {
        "async_pipelines_running": async_running,
        "requests_in_flight": requests_in_flight,
        "cdp_non_operator_open": cdp_non_operator_open,
        "cdp_legs_orphaned": cdp_legs_orphaned,
        "cdp_legs_past_horizon": cdp_legs_past_horizon,
        "cdp_legs_past_horizon_count": cdp_legs_past_horizon_count,
        "total": total,
        "busy": total > 0,
    }
    if cdp_legs_error is not None:
        content["cdp_legs_error"] = cdp_legs_error
    return JSONResponse(status_code=200, content=content)
