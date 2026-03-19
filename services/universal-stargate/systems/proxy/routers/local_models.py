"""Local models browser — UI and API for viewing models per node.

Serves a static HTML/JS/CSS browser at /local-ui and provides
GET /api/v1/local-models with node-grouped model data aggregated
from catalog caches (no I/O — reads WebSocket + federation state).
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
)

from ..dependencies import get_auth_dependency, get_proxy

if TYPE_CHECKING:
    from fastapi import FastAPI

    from ..stargate_core import StargateProxy

logger = get_logger(__name__)

_STATIC_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent / "static" / "local-models"
)

api_router = APIRouter(prefix="/api/v1", tags=["local-models"])
ui_router = APIRouter(tags=["local-ui"])


def _build_node_models_response(proxy: StargateProxy) -> dict:
    """Aggregate model-per-node data from catalog caches.

    Inverts the source map (model -> nodes) into a node-grouped structure
    enriched with context metadata and activation status.
    """
    federation_integration = get_federation_integration()
    if federation_integration and federation_integration.config:
        local_id = federation_integration.config.stargate_id
    else:
        local_id = "local"

    source_map = get_model_source_map(
        local_id,
        proxy.gateway_manager,
        proxy.federated_manager,
    )

    context_metadata = get_model_context_metadata(
        proxy.gateway_manager,
        proxy.federated_manager,
    )

    activated_models = get_activated_models_for_display(
        proxy.gateway_manager,
        proxy.federated_manager,
    )

    if proxy.pipeline_registry:
        pipeline_ids = sorted(proxy.pipeline_registry.pipelines.keys())
    else:
        pipeline_ids = []

    node_models: dict[str, list[dict]] = {}
    for model_id, node_ids in source_map.items():
        meta = context_metadata.get(model_id, {})
        entry: dict = {
            "id": model_id,
            "type": "model",
            "activated": model_id in activated_models,
        }
        if "context_length" in meta:
            entry["context_length"] = meta["context_length"]
        if "effective_context_per_slot" in meta:
            entry["effective_context_per_slot"] = meta["effective_context_per_slot"]

        for node_id in node_ids:
            node_models.setdefault(node_id, []).append(entry)

    nodes = []
    for node_id in sorted(node_models):
        models = sorted(node_models[node_id], key=lambda m: m["id"])
        nodes.append(
            {
                "node_id": node_id,
                "models": models,
                "model_count": len(models),
            }
        )

    unique_model_ids = set()
    for model_list in node_models.values():
        for m in model_list:
            unique_model_ids.add(m["id"])

    return {
        "nodes": nodes,
        "pipelines": pipeline_ids,
        "total_models": len(unique_model_ids),
        "total_nodes": len(nodes),
    }


@api_router.get("/local-models")
async def list_local_models(
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict = Depends(get_auth_dependency),
) -> JSONResponse:
    """Node-grouped local model listing for the browser UI."""
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
