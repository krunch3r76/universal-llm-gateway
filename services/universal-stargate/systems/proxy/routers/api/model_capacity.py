"""Model capacity endpoint.

GET /api/v1/model-capacity/{model_id}

Returns the total parallel inference slots available across all nodes currently
serving the model (loaded + busy).  Callers use this to size fan-out concurrency
(e.g. pipeline MapExecutor max_concurrency) so requests are submitted at the rate
the cluster can actually absorb rather than flooding the CapacityPool queue.

∀ node: contributes parallel_slots iff model is present in model_resources
(derived from GATEWAY_SNAPSHOT telemetry) AND the node is in loaded/busy state.
Nodes that carry no catalog entry (cloud gateways, offline nodes) are excluded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException

from systems.federation import get_federation_integration
from systems.routing.selection.catalog import (
    get_all_available_models,
    get_model_source_map,
    get_model_status_map,
)

from ...dependencies import get_auth_dependency, get_proxy

if TYPE_CHECKING:
    from ...stargate_core import StargateProxy

router = APIRouter(prefix="/model-capacity", tags=["model-capacity"])


def _resolve_local_id() -> str:
    fed = get_federation_integration()
    return fed.config.stargate_id if fed and fed.config else "local"


@router.get("/{model_id:path}")
async def get_model_capacity(
    model_id: str,
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict = Depends(get_auth_dependency),
) -> dict[str, Any]:
    """Total parallel inference slots available across all nodes serving model_id.

    Only nodes where the model is currently loaded or busy contribute.  The sum
    is the correct upper bound for pipeline fan-out: submitting more concurrent
    requests than this saturates the CapacityPool and causes long queue waits.

    Response shape:
      {
        "model_id": "qwen3-14b-q4-k-m",
        "total_parallel_slots": 10,
        "nodes": [
          {"node_id": "jupiter", "parallel_slots": 5, "status": "loaded"},
          {"node_id": "localhost", "parallel_slots": 5, "status": "loaded"}
        ]
      }
    """
    local_id = _resolve_local_id()
    gm, fm = proxy.gateway_manager, proxy.federated_manager
    all_models = get_all_available_models(gm, fm)
    source_map = get_model_source_map(local_id, gm, fm)

    if model_id not in all_models and model_id not in source_map:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")

    status_map = get_model_status_map(local_id, gm, fm)
    summary = status_map.get(model_id, {})
    active_nodes: set[str] = set(summary.get("loaded_on", [])) | set(
        summary.get("busy_on", [])
    )

    gateways = fm.get_all_gateways() if fm else []
    nodes: list[dict[str, Any]] = []
    total_slots = 0
    for gw in gateways:
        meta = gw.model_resources.get(model_id)
        if meta is None:
            continue
        node_id = gw.node_id or gw.gateway_id
        if node_id not in active_nodes:
            continue
        slots = meta.get("parallel_slots", 1)
        status = "busy" if node_id in summary.get("busy_on", []) else "loaded"
        nodes.append({"node_id": node_id, "parallel_slots": slots, "status": status})
        total_slots += slots

    return {
        "model_id": model_id,
        "total_parallel_slots": total_slots,
        "nodes": sorted(nodes, key=lambda n: n["node_id"]),
    }
