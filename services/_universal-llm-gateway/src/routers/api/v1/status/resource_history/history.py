"""Resource history route returning current and peak usage snapshots for a model."""

from fastapi import Depends, HTTPException, Query

from src.routers.dependencies import get_worker_controller

from .deps import logger, router
from .guards import require_resource_monitor
from .snapshots import build_history_snapshots, fetch_model_usage


@router.get("/models/{model_id}/resource-history")
async def get_model_resource_history(
    model_id: str,
    limit: int = Query(
        100, ge=1, le=1000, description="Maximum number of snapshots to return"
    ),
    since: float | None = Query(
        None, description="Filter snapshots after this timestamp (Unix timestamp)"
    ),
    worker_controller=Depends(get_worker_controller),
):
    """
    Get resource usage history for a specific model.

    Args:
        model_id: Model identifier
        limit: Maximum number of snapshots to return (1-1000)
        since: Filter snapshots after this timestamp (Unix timestamp)

    Returns:
        Dict containing resource snapshots with worker configuration
    """
    try:
        require_resource_monitor(worker_controller)

        current_usage, peak_usage = await fetch_model_usage(
            worker_controller, model_id, logger
        )

        if not current_usage and not peak_usage:
            return {
                "model_id": model_id,
                "snapshots": [],
                "total_snapshots": 0,
                "limit": limit,
                "since": since,
                "message": "No resource data available",
            }

        snapshots = build_history_snapshots(current_usage, peak_usage)

        return {
            "model_id": model_id,
            "snapshots": snapshots,
            "total_snapshots": len(snapshots),
            "limit": limit,
            "since": since,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to get resource history for {model_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
