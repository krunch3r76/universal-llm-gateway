"""Resource statistics route summarizing worker and system usage for a model."""

from fastapi import Depends, HTTPException, Query

from src.routers.dependencies import get_worker_controller

from .deps import logger, router
from .guards import require_resource_monitor
from .snapshots import build_stats_snapshots, fetch_model_usage
from .stats_aggregation import build_resource_stats_response


@router.get("/models/{model_id}/resource-stats")
async def get_model_resource_stats(
    model_id: str,
    since: float | None = Query(
        None, description="Filter snapshots after this timestamp (Unix timestamp)"
    ),
    worker_controller=Depends(get_worker_controller),
):
    """
    Get resource usage statistics for a specific model.

    Args:
        model_id: Model identifier
        since: Filter snapshots after this timestamp (Unix timestamp)

    Returns:
        Dict containing resource statistics
    """
    try:
        require_resource_monitor(worker_controller)

        current_usage, peak_usage = await fetch_model_usage(
            worker_controller, model_id, logger
        )

        if not current_usage and not peak_usage:
            return {
                "model_id": model_id,
                "total_snapshots": 0,
                "message": "No resource data available",
            }

        snapshots = build_stats_snapshots(current_usage, peak_usage)

        if not snapshots:
            return {
                "model_id": model_id,
                "total_snapshots": 0,
                "message": "No resource data available",
            }

        return build_resource_stats_response(model_id, snapshots)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to get resource stats for {model_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
