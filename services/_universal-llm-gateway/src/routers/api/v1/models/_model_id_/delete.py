"""DELETE /api/v1/models/{model_id} - Unload model endpoint"""

from universal_logging import get_logger
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from src.core.workers import WorkerController
from src.routers.dependencies import get_worker_controller

router = APIRouter(prefix="/v1/models", tags=["Model Management"])
logger = get_logger(__name__)


@router.delete("/{model_id}")
async def unload_model(
    model_id: str,
    force: Annotated[bool, Query(description="Force kill process immediately")] = False,
    worker_controller: WorkerController = Depends(get_worker_controller),
):
    """
    Unload a model from memory.

    This endpoint unloads the specified model from the worker process,
    freeing up memory and resources. The model can be reloaded later
    when needed (if auto_load_on_request is enabled).

    **Behavior:**
    - `force=False` (default): Graceful shutdown, skips if model busy
    - `force=True`: Kills process immediately, bypasses busy check

    Both paths emit MODEL_UNLOADED event on completion.
    Caller should wait for event to confirm resources freed.

    Args:
        model_id: The ID of the model to unload
        force: If True, force kill process immediately (for eviction)

    Returns:
        JSON response with:
        - message: Human-readable result message
        - model_id: The model identifier
        - status: One of "unloaded", "skipped", "not_loaded"
        - reason: Detailed reason code (e.g., "model_busy", "unloaded")
        - force: Boolean indicating which path was used

    Raises:
        HTTPException: If model unloading fails (500 status code)
    """
    try:
        # Check model state - we should unload even if in ERROR state
        # to clean up zombie processes and free VRAM
        from src.core.resources import resource_tracker

        model_info = resource_tracker.get_model_info(model_id)

        if model_info:
            current_status = model_info.status.value
            # If model is in ERROR state, we still need to unload to clean up
            if current_status == "error":
                logger.info(
                    f"Model '{model_id}' in ERROR state, "
                    "unloading to clean up and free resources"
                )
            elif current_status in ("unloaded", "not_loaded"):
                return {
                    "message": f"Model '{model_id}' is not currently loaded",
                    "model_id": model_id,
                    "status": "not_loaded",
                }

        # Check if model is currently loaded (process exists and is alive)
        is_loaded = await worker_controller.is_model_loaded(model_id)

        if (
            not is_loaded
            and model_info
            and model_info.status.value not in ("error", "unloading")
        ):
            # Process is dead and not in ERROR/UNLOADING - already unloaded
            return {
                "message": f"Model '{model_id}' is not currently loaded",
                "model_id": model_id,
                "status": "not_loaded",
            }

        # Pass force parameter - bypasses busy check, uses fast kill
        result = await worker_controller.unload_model(model_id, force=force)

        if result.success:
            return {
                "message": f"Model '{model_id}' unloaded successfully",
                "model_id": model_id,
                "status": "unloaded",
                "reason": result.reason,
                "force": force,  # Inform caller which path was used
            }
        elif result.skipped:
            return {
                "message": f"Model '{model_id}' unload skipped: {result.reason}",
                "model_id": model_id,
                "status": "skipped",
                "reason": result.reason,
            }
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to unload model '{model_id}': {result.reason}",
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error unloading model '{model_id}': {str(e)}"
        )
