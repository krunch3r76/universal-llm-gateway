"""Node-grouped models browser — UI and API for viewing models per node.

Serves a static HTML/JS/CSS browser at /local-ui and provides
GET /api/v1/node-models with node-grouped model data aggregated
from catalog caches (no I/O — reads WebSocket + federation state).
Includes local and federated models from all reachable nodes.

Each model entry includes a ``status`` field indicating whether the model
is loaded, busy, or loading on the node.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from universal_logging import get_logger

from systems.federation import get_federation_integration
from systems.routing.selection.catalog import (
    get_activated_models_for_display,
    get_model_context_metadata,
    get_model_source_map,
    get_model_status_map,
)

from ..dependencies import get_auth_dependency, get_proxy

if TYPE_CHECKING:
    from fastapi import FastAPI

    from ..stargate_core import StargateProxy

logger = get_logger(__name__)

_STATIC_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent / "static" / "local-models"
)

api_router = APIRouter(prefix="/api/v1", tags=["node-models"])
ui_router = APIRouter(tags=["local-ui"])


def _status_label_for_node(
    node_id: str, model_status: dict[str, list[str]]
) -> tuple[str, bool]:
    """Compute display status for one model on one node from telemetry buckets.

    Returns a UI label with precedence busy, then loading, then loaded, then
    available, plus whether the model counts as loaded on this node for summary
    totals.
    """
    is_loaded = node_id in model_status.get("loaded_on", [])
    is_busy = node_id in model_status.get("busy_on", [])
    is_loading = node_id in model_status.get("loading_on", [])
    if is_busy:
        label = "busy"
    elif is_loading:
        label = "loading"
    elif is_loaded:
        label = "loaded"
    else:
        label = "available"
    return label, is_loaded


def _node_models_from_source(
    source_map: dict[str, list[str]],
    context_metadata: dict[str, dict],
    activated_models: set[str],
    status_map: dict[str, dict[str, list[str]]],
) -> tuple[dict[str, list[dict]], set[str]]:
    """Build per-node model rows and the set of model IDs loaded on ≥1 node."""
    node_models: dict[str, list[dict]] = {}
    loaded_model_ids: set[str] = set()
    for model_id, node_ids in source_map.items():
        meta = context_metadata.get(model_id, {})
        model_status = status_map.get(model_id, {})
        for node_id in node_ids:
            status, is_loaded_on_node = _status_label_for_node(node_id, model_status)
            if is_loaded_on_node:
                loaded_model_ids.add(model_id)
            entry: dict = {
                "id": model_id,
                "type": "model",
                "activated": model_id in activated_models,
                "status": status,
            }
            if "context_length" in meta:
                entry["context_length"] = meta["context_length"]
            if "effective_context_per_slot" in meta:
                entry["effective_context_per_slot"] = meta["effective_context_per_slot"]
            node_models.setdefault(node_id, []).append(entry)
    return node_models, loaded_model_ids


def _build_node_models_response(proxy: StargateProxy) -> dict:
    """Build the JSON for GET /api/v1/node-models from catalog-backed caches.

    Groups models by node, merges context metadata and activation flags, attaches
    per-node load status from ``get_model_status_map``, and returns aggregate
    counts including how many distinct models are loaded on at least one node.
    """
    fed = get_federation_integration()
    local_id = fed.config.stargate_id if fed and fed.config else "local"
    gm, fm = proxy.gateway_manager, proxy.federated_manager
    source_map = get_model_source_map(local_id, gm, fm)
    context_metadata = get_model_context_metadata(gm, fm)
    activated_models = get_activated_models_for_display(gm, fm)
    status_map = get_model_status_map(local_id, gm, fm)
    pipeline_ids = (
        sorted(proxy.pipeline_registry.pipelines.keys())
        if proxy.is_pipeline_system_ready and proxy.pipeline_registry
        else []
    )
    node_models, loaded_model_ids = _node_models_from_source(
        source_map, context_metadata, activated_models, status_map
    )

    nodes = [
        {
            "node_id": nid,
            "models": sorted(node_models[nid], key=lambda m: m["id"]),
            "model_count": len(node_models[nid]),
        }
        for nid in sorted(node_models)
    ]

    unique_model_ids = {
        m["id"] for model_list in node_models.values() for m in model_list
    }

    return {
        "nodes": nodes,
        "pipelines": pipeline_ids,
        "total_models": len(unique_model_ids),
        "loaded_models": len(loaded_model_ids),
        "total_nodes": len(nodes),
    }


@api_router.get("/node-models")
async def list_node_models(
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict = Depends(get_auth_dependency),
) -> JSONResponse:
    """Node-grouped model listing (local + federated) for the browser UI."""
    data = _build_node_models_response(proxy)
    return JSONResponse(content=data)


@ui_router.get("/local-ui")
async def local_ui_index() -> FileResponse:
    """Serve the local models browser UI."""
    return FileResponse(
        _STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-cache"},
    )


def mount_static(app: FastAPI) -> None:
    """Mount static assets for the local models browser.

    Called from app.py after router registration so the mount
    does not shadow API routes.
    """
    if _STATIC_DIR.exists():
        app.mount(
            "/local-ui/static",
            StaticFiles(directory=str(_STATIC_DIR)),
            name="local-models-static",
        )
