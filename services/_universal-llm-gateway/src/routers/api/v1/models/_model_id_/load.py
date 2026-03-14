"""POST /api/v1/models/{model_id}/load - Load model endpoint"""

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from universal_logging import get_logger

from src.core.errors import (
    GatewayError,
    ModelLoadingError,
    SyntaxErrorException,
    WorkerInitializationError,
    create_error_response,
)
from src.core.model_registry import ModelRegistry
from src.core.workers import WorkerController
from src.routers.dependencies import get_model_registry, get_worker_controller

router = APIRouter(prefix="/v1/models", tags=["Model Management"])
logger = get_logger(__name__)


@router.post("/{model_id}/load")
async def load_model(
    model_id: str,
    response: Response,
    worker_controller: WorkerController = Depends(get_worker_controller),
    model_registry: ModelRegistry = Depends(get_model_registry),
):
    """
    Explicitly load a model into memory with async operation support.

    This endpoint initiates model loading and returns immediately with status:
    - 200 OK: Model already loaded
    - 202 Accepted: Loading started, poll GET /v1/models/{model_id} for status
    - 404 Not Found: Model not in registry
    - 403 Forbidden: Model disabled

    The endpoint is non-blocking - it triggers loading and returns immediately.
    Client should poll the model status endpoint to check completion.

    This is useful for:
    - Pre-warming models for faster first request
    - Explicitly controlling which model is loaded
    - Ensuring a model is ready before starting a workload

    In single-model mode (max_concurrent_workers=1), loading a new model will
    automatically unload any currently loaded model first.

    Args:
        model_id: The ID of the model to load

    Returns:
        - 200: Model already loaded (immediate)
        - 202: Loading started (async, poll for status)

    Raises:
        HTTPException: 404/403 for invalid models, 500 for internal errors
    """
    request_id: str = str(uuid.uuid4())[:8]

    try:
        # Verify model exists in catalog
        if not model_registry.find_config_key_for_openai_id(model_id):
            raise HTTPException(
                status_code=404,
                detail=f"Model '{model_id}' not found in registry. Use /v1/models to see available models.",
            )
        if not model_registry.is_model_enabled(model_id):
            raise HTTPException(
                status_code=403,
                detail=f"Model '{model_id}' is disabled. Enable it in the configuration to load.",
            )

        # Check if model is already loaded
        is_loaded = await worker_controller.is_model_loaded(model_id)

        if is_loaded:
            # Route through coalescing loader so idempotent loads emit
            # MODEL_LOADED for WebSocket waiters.
            await worker_controller.load_model(model_id)
            return {
                "message": f"Model '{model_id}' is already loaded",
                "model_id": model_id,
                "status": "loaded",
            }

        # Check model status before attempting load
        from src.core.resources import resource_tracker

        model_info = resource_tracker.get_model_info(model_id)

        if model_info:
            current_status = model_info.status.value

            # If model is in ERROR state, log it but allow retry
            # The error state will be cleared when we call set_model_loading()
            if current_status == "error":
                error_msg = model_info.error_message or "Model is in error state"
                # Log the previous error for debugging
                logger.info(
                    f"Model '{model_id}' is in ERROR state: {error_msg}. "
                    f"Allowing retry - error state will be cleared."
                )
                # Continue with load attempt (error state will be reset)

            # If model is currently loading, return 202
            elif current_status == "loading":
                response.status_code = 202
                return {
                    "message": f"Model '{model_id}' is currently loading",
                    "model_id": model_id,
                    "status": "loading",
                }

            # If model is BUSY, cannot load (invalid state transition)
            elif current_status == "busy":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": {
                            "message": f"Model '{model_id}' is currently busy processing a request. Wait for completion before reloading.",
                            "type": "model_busy",
                            "code": "model_busy_cannot_load",
                            "model": model_id,
                            "current_status": current_status,
                        }
                    },
                )

            # If model is UNLOADING, cannot load (invalid state transition)
            elif current_status == "unloading":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": {
                            "message": f"Model '{model_id}' is currently unloading. Wait for unload to complete before loading.",
                            "type": "model_unloading",
                            "code": "model_unloading_cannot_load",
                            "model": model_id,
                            "current_status": current_status,
                        }
                    },
                )

        # Set state to "loading" BEFORE creating the task to prevent race conditions
        # This ensures concurrent requests will see the loading state
        # Note: set_model_loading() now automatically clears ERROR state if present
        resource_tracker.set_model_loading(model_id)

        # Fetch requirements before create_task so any error is reported to the client
        resources = resource_tracker.get_model_requirements(model_id)

        # Trigger async load (non-blocking)
        asyncio.create_task(worker_controller.load_model(model_id))

        # Return immediately with 202 Accepted
        response.status_code = 202

        return {
            "message": f"Model '{model_id}' loading started",
            "model_id": model_id,
            "status": "loading",
            "estimated_load_time_seconds": resources.get("estimated_load_time", 10),
        }

    except HTTPException:
        raise
    except (
        WorkerInitializationError,
        ModelLoadingError,
        SyntaxErrorException,
        GatewayError,
    ) as e:
        # Enhanced error with full details
        context = {
            "operation": "model_loading",
            "model_id": model_id,
            "component": "model_router",
            "request_id": request_id,
        }
        raise create_error_response(e, 500, context)
    except Exception as e:
        logger.exception("Unhandled error during model loading") # Added logging
        # Generic error with enhanced details
        context = {
            "operation": "model_loading",
            "model_id": model_id,
            "component": "model_router",
            "request_id": request_id,
        }
        raise create_error_response(e, 500, context)
