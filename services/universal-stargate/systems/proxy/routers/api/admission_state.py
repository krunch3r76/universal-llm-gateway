"""Admission state snapshot endpoint for external coordination consumers.

GET /api/v1/admission/state?model_id=<routing_key>

Returns a point-in-time snapshot of Stargate's admission and load state for
the requested model.  Designed for RAG's AdmissionGate startup snapshot so it
can pre-seed per-model gate state before the WebSocket subscription begins,
closing the startup-snapshot race described in
`todo:rag-admission-gate-startup-snapshot`.

Response is advisory: callers MUST proceed regardless of the response value.
The per-request X-Request-Timeout enforced by Stargate is the correctness
backstop.  This endpoint is a coordination hint, not a gating mechanism.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from systems.federation import get_federation_integration
from systems.routing.selection.catalog import get_model_status_map

from ...dependencies import get_auth_dependency, get_proxy

if TYPE_CHECKING:
    from ...stargate_core import StargateProxy

router = APIRouter(prefix="/admission", tags=["admission"])


def _resolve_local_id() -> str:
    fed = get_federation_integration()
    return fed.config.stargate_id if fed and fed.config else "local"


@router.get("/state")
async def get_admission_state(
    model_id: str = Query(..., description="routing_key of the model to query"),
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict = Depends(get_auth_dependency),
) -> dict[str, Any]:
    """Point-in-time admission and load state for one model.

    Aggregates CapacityPool admission state and gateway load/loading status.

    Fields:
        model_id: the routing_key as received (echoed back)
        paused: True iff CapacityPool has suspended admission for this model
        paused_reason: active pause reason string, or null
        paused_until_ms: estimated Unix-ms deadline for the pause, or null
        loading: True iff any gateway reports this model currently cold-loading
        loaded: True iff any gateway reports this model loaded or busy
        queue_depth: number of requests currently queued in CapacityPool

    Note on model_id normalization: callers should pass the routing_key
    (the form used at the admission layer), not a catalog alias or display name,
    to ensure the CapacityPool lookup matches the event-stream routing_key.
    Use the same normalization the AdmissionGate uses (ModelId.routing_key).
    """
    capacity_pool = getattr(proxy, "capacity_pool", None)
    if capacity_pool is None:
        raise HTTPException(
            status_code=503,
            detail="Capacity pool not available on this Stargate node",
        )

    admission = capacity_pool.get_admission_state(model_id)

    local_id = _resolve_local_id()
    gm = proxy.gateway_manager
    fm = getattr(proxy, "federated_manager", None)
    status_map = get_model_status_map(local_id, gm, fm)
    model_status = status_map.get(
        model_id, {"loaded_on": [], "busy_on": [], "loading_on": []}
    )

    loaded = bool(model_status["loaded_on"] or model_status["busy_on"])
    loading = bool(model_status["loading_on"])

    return {
        "model_id": model_id,
        "paused": admission["paused"],
        "paused_reason": admission["paused_reason"],
        "paused_until_ms": admission["paused_until_ms"],
        "loading": loading,
        "loaded": loaded,
        "queue_depth": admission["queue_depth"],
    }
