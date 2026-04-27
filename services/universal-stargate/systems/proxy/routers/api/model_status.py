"""Model status API — per-model load/busy/loading placement across all nodes.

GET /api/v1/model-status       → all models with placement summary
GET /api/v1/model-status/{id}  → single model detail with per-node hardware
                                  resources (vram_mb, parallel_slots,
                                  effective_context_per_slot, vram_free_mb)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from systems.federation import get_federation_integration
from systems.federation.common.types import FederatedGateway
from systems.routing.selection.catalog import (
    get_activated_models_for_display,
    get_all_available_models,
    get_model_source_map,
    get_model_status_map,
)

from ...dependencies import get_auth_dependency, get_proxy

if TYPE_CHECKING:
    from ...stargate_core import StargateProxy

router = APIRouter(prefix="/model-status", tags=["model-status"])


def _resolve_local_id() -> str:
    """Stargate node ID for the local node."""
    fed = get_federation_integration()
    return fed.config.stargate_id if fed and fed.config else "local"


def _aggregate_status(summary: dict[str, list[str]]) -> str:
    """Derive a single status label from per-node placement lists.

    Precedence: busy > loading > loaded > available.
    """
    if summary.get("busy_on"):
        return "busy"
    if summary.get("loading_on"):
        return "loading"
    if summary.get("loaded_on"):
        return "loaded"
    return "available"


def _build_model_status_entry(
    model_id: str,
    source_map: dict[str, list[str]],
    status_map: dict[str, dict[str, list[str]]],
    activated_models: set[str],
    all_models: set[str],
) -> dict[str, Any]:
    """Build one model-status response entry with per-node detail."""
    summary = status_map.get(
        model_id,
        {"loaded_on": [], "busy_on": [], "loading_on": []},
    )
    node_ids = source_map.get(model_id, [])
    nodes = []
    for nid in sorted(node_ids):
        is_loaded = nid in summary.get("loaded_on", [])
        is_busy = nid in summary.get("busy_on", [])
        is_loading = nid in summary.get("loading_on", [])
        if is_busy:
            node_status = "busy"
        elif is_loading:
            node_status = "loading"
        elif is_loaded:
            node_status = "loaded"
        else:
            node_status = "available"
        nodes.append({"node_id": nid, "status": node_status})

    return {
        "id": model_id,
        "status": _aggregate_status(summary),
        "activated": model_id in activated_models,
        "available": model_id in all_models,
        "summary": {
            "loaded_on": sorted(summary.get("loaded_on", [])),
            "busy_on": sorted(summary.get("busy_on", [])),
            "loading_on": sorted(summary.get("loading_on", [])),
        },
        "nodes": nodes,
    }


@router.get("")
async def list_model_status(
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict = Depends(get_auth_dependency),
    status: str | None = Query(
        None,
        description="Filter by status: loaded, busy, loading, available",
    ),
) -> dict[str, Any]:
    """All models with per-model load/busy/loading placement across nodes.

    Model-centric complement to the node-grouped ``/api/v1/node-models``.
    Useful for debugging routing decisions and verifying what's loaded where.
    """
    local_id = _resolve_local_id()
    gm, fm = proxy.gateway_manager, proxy.federated_manager
    source_map = get_model_source_map(local_id, gm, fm)
    status_map = get_model_status_map(local_id, gm, fm)
    activated_models = get_activated_models_for_display(gm, fm)
    all_models = get_all_available_models(gm, fm)

    all_model_ids = sorted(set(source_map) | all_models)
    entries = [
        _build_model_status_entry(
            mid,
            source_map,
            status_map,
            activated_models,
            all_models,
        )
        for mid in all_model_ids
    ]

    if status:
        entries = [e for e in entries if e["status"] == status]

    loaded_count = sum(1 for e in entries if e["status"] in ("loaded", "busy"))

    return {
        "models": entries,
        "total": len(entries),
        "loaded": loaded_count,
    }


def _collect_hardware_by_node(
    model_id: str,
    gateways: list[FederatedGateway],
) -> dict[str, dict[str, Any]]:
    """Collect per-node hardware profile for a model from gateway telemetry.

    ∀ gw ∈ gateways: if model_id ∈ gw.model_resources → include catalog entry
    (context_length, parallel_slots, effective_context_per_slot, vram_mb,
    ram_mb) alongside current VRAM state (vram_free_mb, vram_total_mb).

    Keys are node_id strings; entries are absent for gateways that have no
    catalog entry for this model (e.g. cloud gateways, offline nodes).
    """
    result: dict[str, dict[str, Any]] = {}
    for gw in gateways:
        # model_resources is populated from GATEWAY_SNAPSHOT telemetry;
        # keys are model_id strings as sent by the edge.
        meta = gw.model_resources.get(model_id)
        if meta is None:
            continue
        node_key = gw.node_id or gw.gateway_id
        entry: dict[str, Any] = {}
        for field in (
            "context_length",
            "parallel_slots",
            "effective_context_per_slot",
        ):
            if field in meta:
                entry[field] = meta[field]
        if "vram_usage" in meta:
            entry["vram_mb"] = meta["vram_usage"]
        if "ram_usage" in meta:
            entry["ram_mb"] = meta["ram_usage"]
        entry["vram_free_mb"] = gw.vram_free_mb
        entry["vram_total_mb"] = gw.vram_total_mb
        result[node_key] = entry
    return result


@router.get("/{model_id:path}")
async def get_model_status(
    model_id: str,
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict = Depends(get_auth_dependency),
) -> dict[str, Any]:
    """Status detail for a single model: placement, load state, per-node breakdown.

    The ``hardware`` field exposes catalog-derived resource requirements pulled
    from GATEWAY_SNAPSHOT telemetry — keyed by node_id.  Each entry contains:

    - ``context_length``            — configured context window (tokens)
    - ``parallel_slots``            — number of simultaneous inference slots
                                      (absent when 1 / default)
    - ``effective_context_per_slot``— per-slot token budget
                                      (context_length // parallel_slots)
    - ``vram_mb``                   — VRAM this variant consumes when loaded
    - ``ram_mb``                    — host RAM consumed
    - ``vram_free_mb``              — currently free VRAM on this gateway
    - ``vram_total_mb``             — total VRAM on this gateway

    These are absent for cloud or offline nodes that carry no catalog entry.
    """
    local_id = _resolve_local_id()
    gm, fm = proxy.gateway_manager, proxy.federated_manager
    all_models = get_all_available_models(gm, fm)
    source_map = get_model_source_map(local_id, gm, fm)

    if model_id not in all_models and model_id not in source_map:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")

    status_map = get_model_status_map(local_id, gm, fm)
    activated_models = get_activated_models_for_display(gm, fm)

    entry = _build_model_status_entry(
        model_id,
        source_map,
        status_map,
        activated_models,
        all_models,
    )

    gateways = fm.get_all_gateways() if fm else []
    hardware = _collect_hardware_by_node(model_id, gateways)
    if hardware:
        entry["hardware"] = hardware

    return entry
