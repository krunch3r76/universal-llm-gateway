"""
Management API endpoints for streaming cancellation.

Provides endpoints for cancelling active streaming requests and monitoring
active streams.
"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from universal_logging import get_logger

from src.core.workers.controller import WorkerController
from src.routers.dependencies import get_worker_controller

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/management", tags=["management"])


class CancelRequest(BaseModel):
    """Request model for cancelling a streaming request."""

    stream_id: str
    reason: str | None = "explicit_cancellation"


class CancelResponse(BaseModel):
    """Response model for cancellation requests."""

    success: bool
    stream_id: str
    reason: str
    message: str


@router.post("/models/{model_id}/cancel-stream")
async def cancel_stream(
    model_id: str, worker_controller: WorkerController = Depends(get_worker_controller)
) -> dict[str, Any]:
    """
    Cancel the active streaming request for a specific model.

    Since each model runs in a single worker process, there can only be
    one active stream per model at a time. This endpoint cancels whatever
    stream is currently active on the specified model.

    Args:
        model_id: Model identifier
        worker_controller: Worker controller dependency

    Returns:
        Dict with cancellation result
    """
    logger.info(f"🛑 Cancellation request for model {model_id}")
    try:
        # Check if model has a supervisor
        from src.core.resources import resource_tracker

        model_info = resource_tracker.get_model_info(model_id)
        if not model_info:
            logger.warning(f"❌ No model info found for {model_id}")
            return {
                "success": False,
                "model_id": model_id,
                "message": f"Model {model_id} not found in resource tracker",
            }

        logger.info(f"🔍 Model {model_id} status: {model_info.status}")

        # Cancel the active work on the specified model (unified API)
        success = await worker_controller.cancel_work(
            model_id, reason="management_api_request"
        )

        if success:
            logger.info(f"✅ Successfully cancelled stream on {model_id}")
            return {
                "success": True,
                "model_id": model_id,
                "message": f"Model {model_id} marked as idle (stream cancelled or no active work)",
            }
        else:
            logger.warning(
                f"⚠️ Cancellation returned False for {model_id} (model may already be idle)"
            )
            return {
                "success": False,
                "model_id": model_id,
                "message": f"Failed to communicate with model {model_id} (model may already be idle or not loaded)",
            }

    except Exception as e:
        logger.error(
            f"❌ Error cancelling stream on model {model_id}: {e}", exc_info=True
        )
        return {
            "success": False,
            "model_id": model_id,
            "message": f"Failed to cancel stream: {str(e)}",
        }


@router.post("/models/{model_id}/cancel-request")
async def cancel_request(
    model_id: str,
    request_body: dict[str, Any],
    worker_controller: WorkerController = Depends(get_worker_controller),
) -> dict[str, Any]:
    """
    Cancel a specific request by request_id/stream_id.

    Accepts:
        - stream_id: Stream/request identifier
        - reason: Cancellation reason (optional)

    Args:
        model_id: Model identifier
        request_body: Dict containing stream_id or request_id
        worker_controller: Worker controller dependency

    Returns:
        Dict with cancellation result
    """
    stream_id = request_body.get("stream_id") or request_body.get("request_id")
    reason = request_body.get("reason", "explicit_cancellation")

    if not stream_id:
        return {
            "success": False,
            "model_id": model_id,
            "message": "stream_id or request_id required",
        }

    logger.info(
        f"🛑 Cancellation request for {model_id}/{stream_id} (reason: {reason})"
    )

    try:
        success = await worker_controller.cancel_work(
            model_id, stream_id=stream_id, reason=reason
        )

        if success:
            logger.info(f"✅ Successfully cancelled request {stream_id} on {model_id}")
            return {
                "success": True,
                "model_id": model_id,
                "stream_id": stream_id,
                "message": f"Request {stream_id} cancelled",
            }
        else:
            return {
                "success": False,
                "model_id": model_id,
                "stream_id": stream_id,
                "message": "Cancellation failed - request may have already completed",
            }

    except Exception as e:
        logger.error(f"❌ Error cancelling request: {e}", exc_info=True)
        return {
            "success": False,
            "model_id": model_id,
            "stream_id": stream_id,
            "message": f"Cancellation error: {str(e)}",
        }
