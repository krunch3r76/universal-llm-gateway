"""POST /api/v1/models/{model_id}/cleanup - Force cleanup orphaned process

Manual intervention endpoint for when automatic cleanup fails.
Should rarely be needed if load flow cleanup is working correctly.
"""

from fastapi import APIRouter, Depends, HTTPException
from universal_logging import get_logger

from src.core.workers import WorkerController
from src.routers.dependencies import get_worker_controller

router = APIRouter(prefix="/api/v1/models", tags=["Model Management"])
logger = get_logger(__name__)


@router.post("/{model_id}/cleanup")
async def force_cleanup_process(
    model_id: str,
    worker_controller: WorkerController = Depends(get_worker_controller),
):
    """
    Force cleanup an orphaned worker process.

    ⚠️ MANUAL INTERVENTION: This should rarely be needed.
    If you're calling this regularly, investigate why
    cleanup_failed_worker isn't working.

    This endpoint:
    1. Kills process via PID (SIGKILL)
    2. Removes from resource tracker
    3. Cleans up socket file
    4. Returns immediately (no event wait)
    """
    try:
        process_info = worker_controller.get_all_process_info().get(model_id, {})

        if not process_info:
            return {
                "message": f"No process found for model '{model_id}'",
                "model_id": model_id,
                "status": "no_process",
            }

        pid = process_info.get("pid") if isinstance(process_info, dict) else None

        # Force cleanup
        success = await worker_controller.cleanup_orphaned_process(model_id)

        if success:
            logger.warning(
                f"⚠️ Manual cleanup invoked for {model_id} (PID: {pid}). "
                f"Investigate why automatic cleanup failed."
            )
            return {
                "message": f"Force cleaned up process for '{model_id}'",
                "model_id": model_id,
                "status": "cleaned",
                "pid": pid,
            }
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to cleanup process for '{model_id}'",
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error cleaning up process '{model_id}': {str(e)}",
        )
