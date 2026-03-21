"""POST /api/v1/models/{model_id}/load — validation + async dispatch.

This endpoint is stateless: it validates the request, reads current model
status, and dispatches the load to the loader (which owns all state
transitions). No state mutations occur here.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Response

from src.core.model_registry import ModelRegistry
from src.core.workers import WorkerController
from src.core.workers.model_operations import load_flow
from src.routers.dependencies import get_model_registry, get_worker_controller

router = APIRouter(prefix="/v1/models", tags=["Model Management"])


@router.post("/{model_id}/load")
async def load_model(
    model_id: str,
    response: Response,
    worker_controller: WorkerController = Depends(get_worker_controller),
    model_registry: ModelRegistry = Depends(get_model_registry),
) -> dict:
    """Initiate model loading (non-blocking).

    Validates the request and dispatches to the loader which owns all state
    transitions. Returns immediately — caller polls GET /v1/models/{model_id}
    for completion.

    Returns:
        200 if already loaded, 202 if load dispatched.

    Raises:
        HTTPException: 404 (not found), 403 (disabled), 409 (busy/unloading),
            503 (cleanup in progress).
    """
    if not model_registry.find_config_key_for_openai_id(model_id):
        raise HTTPException(
            status_code=404,
            detail=f"Model '{model_id}' not found in registry. "
            "Use /v1/models to see available models.",
        )
    if not model_registry.is_model_enabled(model_id):
        raise HTTPException(
            status_code=403,
            detail=f"Model '{model_id}' is disabled. "
            "Enable it in the configuration to load.",
        )

    from src.core.resources import resource_tracker

    info = resource_tracker.get_model_info(model_id)
    if info:
        s = info.status.value
        if s == "loaded":
            return {
                "message": f"Model '{model_id}' is already loaded",
                "model_id": model_id,
                "status": "loaded",
            }
        if s == "loading":
            response.status_code = 202
            return {
                "message": f"Model '{model_id}' is currently loading",
                "model_id": model_id,
                "status": "loading",
            }
        if s == "busy":
            raise HTTPException(
                409, detail=f"Model '{model_id}' is busy processing a request"
            )
        if s == "unloading":
            raise HTTPException(
                409, detail=f"Model '{model_id}' is currently unloading"
            )

    if load_flow.is_model_cleanup_in_progress(model_id):
        raise HTTPException(
            503, detail=f"Model '{model_id}' cleanup in progress, retry shortly"
        )

    asyncio.create_task(worker_controller.load_model(model_id))
    response.status_code = 202
    return {
        "message": f"Model '{model_id}' loading started",
        "model_id": model_id,
        "status": "loading",
    }
