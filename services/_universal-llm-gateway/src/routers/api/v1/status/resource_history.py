"""
Resource History API - Query resource usage history for models.

Provides endpoints for querying resource monitoring data:
- Model resource snapshots
- Historical VRAM/RAM usage
- Worker configuration association
- Time-based filtering
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from universal_logging import get_logger

from src.routers.dependencies import get_worker_controller

router = APIRouter()
logger = get_logger(__name__)


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
        if not worker_controller or not hasattr(
            worker_controller, "resource_monitor_enabled"
        ):
            raise HTTPException(
                status_code=503, detail="Resource monitoring not available"
            )

        if not worker_controller.resource_monitor_enabled:
            raise HTTPException(
                status_code=503, detail="Resource monitoring not enabled"
            )

        # Get current resource usage from process_ipc
        try:
            current_usage = await worker_controller.get_resource_usage(model_id)
            peak_usage = worker_controller.get_peak_usage(model_id)

            if not current_usage and not peak_usage:
                return {
                    "model_id": model_id,
                    "snapshots": [],
                    "total_snapshots": 0,
                    "limit": limit,
                    "since": since,
                    "message": "No resource data available",
                }

            # Create snapshot from current and peak data
            snapshots = []
            if current_usage:
                snapshots.append(
                    {
                        "timestamp": current_usage.timestamp.isoformat()
                        if hasattr(current_usage.timestamp, "isoformat")
                        else str(current_usage.timestamp),
                        "ram_used_mb": int(current_usage.ram_used / (1024 * 1024)),
                        "vram_used_mb": int(current_usage.vram_used / (1024 * 1024))
                        if current_usage.vram_used
                        else 0,
                        "ram_percent": current_usage.ram_percent,
                        "vram_percent": current_usage.vram_percent
                        if current_usage.vram_percent
                        else 0,
                        "cpu_percent": current_usage.cpu_percent
                        if current_usage.cpu_percent
                        else 0,
                        "type": "current",
                    }
                )

            if peak_usage:
                snapshots.append(
                    {
                        "timestamp": peak_usage.get("peak_timestamp", "unknown"),
                        "ram_used_mb": int(
                            peak_usage.get("peak_ram_bytes", 0) / (1024 * 1024)
                        ),
                        "vram_used_mb": int(
                            peak_usage.get("peak_vram_bytes", 0) / (1024 * 1024)
                        ),
                        "ram_percent": 0,  # Peak doesn't include percentage
                        "vram_percent": 0,  # Peak doesn't include percentage
                        "cpu_percent": 0,
                        "type": "peak",
                    }
                )

        except Exception as e:
            logger.error(f"Failed to get resource data for {model_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

        return {
            "model_id": model_id,
            "snapshots": snapshots,
            "total_snapshots": len(snapshots),
            "limit": limit,
            "since": since,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get resource history for {model_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
        if not worker_controller or not hasattr(
            worker_controller, "resource_monitor_enabled"
        ):
            raise HTTPException(
                status_code=503, detail="Resource monitoring not available"
            )

        if not worker_controller.resource_monitor_enabled:
            raise HTTPException(
                status_code=503, detail="Resource monitoring not enabled"
            )

        # Get current and peak resource usage from process_ipc
        try:
            current_usage = await worker_controller.get_resource_usage(model_id)
            peak_usage = worker_controller.get_peak_usage(model_id)

            if not current_usage and not peak_usage:
                return {
                    "model_id": model_id,
                    "total_snapshots": 0,
                    "message": "No resource data available",
                }

            # Create statistics from current and peak data
            snapshots = []
            if current_usage:
                snapshots.append(
                    {
                        "ram_used_mb": int(current_usage.ram_used / (1024 * 1024)),
                        "vram_used_mb": int(current_usage.vram_used / (1024 * 1024))
                        if current_usage.vram_used
                        else 0,
                        "ram_percent": current_usage.ram_percent,
                        "vram_percent": current_usage.vram_percent
                        if current_usage.vram_percent
                        else 0,
                        "timestamp": current_usage.timestamp,
                    }
                )

            if peak_usage:
                snapshots.append(
                    {
                        "ram_used_mb": int(
                            peak_usage.get("peak_ram_bytes", 0) / (1024 * 1024)
                        ),
                        "vram_used_mb": int(
                            peak_usage.get("peak_vram_bytes", 0) / (1024 * 1024)
                        ),
                        "ram_percent": 0,
                        "vram_percent": 0,
                        "timestamp": peak_usage.get("peak_timestamp", "unknown"),
                    }
                )

        except Exception as e:
            logger.error(f"Failed to get resource stats for {model_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

        if not snapshots:
            return {
                "model_id": model_id,
                "total_snapshots": 0,
                "message": "No resource data available",
            }

        # Calculate worker-specific statistics
        worker_vram_values = [
            s.get("worker_vram_used_mb", 0)
            for s in snapshots
            if s.get("worker_vram_used_mb") is not None
        ]
        worker_ram_values = [
            s.get("worker_ram_used_mb", 0)
            for s in snapshots
            if s.get("worker_ram_used_mb") is not None
        ]

        # Calculate system-wide statistics
        system_vram_values = [
            s.get("system_vram_used_mb", 0)
            for s in snapshots
            if s.get("system_vram_used_mb") is not None
        ]
        system_ram_values = [
            s.get("system_ram_used_mb", 0)
            for s in snapshots
            if s.get("system_ram_used_mb") is not None
        ]

        gpu_util_values = [
            s.get("gpu_utilization", 0)
            for s in snapshots
            if s.get("gpu_utilization") is not None
        ]

        # Get max values
        max_worker_vram = max(
            s.get("worker_vram_max_mb", 0)
            for s in snapshots
            if s.get("worker_vram_max_mb") is not None
        )
        max_worker_ram = max(
            s.get("worker_ram_max_mb", 0)
            for s in snapshots
            if s.get("worker_ram_max_mb") is not None
        )
        max_system_vram = max(
            s.get("system_vram_max_mb", 0)
            for s in snapshots
            if s.get("system_vram_max_mb") is not None
        )
        max_system_ram = max(
            s.get("system_ram_max_mb", 0)
            for s in snapshots
            if s.get("system_ram_max_mb") is not None
        )

        # Get worker config from first snapshot
        worker_config = snapshots[0].get("worker_config", {}) if snapshots else {}

        return {
            "model_id": model_id,
            "total_snapshots": len(snapshots),
            "time_range": {
                "first_snapshot": min(s.get("timestamp", 0) for s in snapshots),
                "last_snapshot": max(s.get("timestamp", 0) for s in snapshots),
            },
            "worker_resources": {
                "vram_usage": {
                    "current_mb": worker_vram_values[-1] if worker_vram_values else 0,
                    "max_mb": max_worker_vram,
                    "avg_mb": sum(worker_vram_values) / len(worker_vram_values)
                    if worker_vram_values
                    else 0,
                    "min_mb": min(worker_vram_values) if worker_vram_values else 0,
                    "max_observed_mb": max(worker_vram_values)
                    if worker_vram_values
                    else 0,
                },
                "ram_usage": {
                    "current_mb": worker_ram_values[-1] if worker_ram_values else 0,
                    "max_mb": max_worker_ram,
                    "avg_mb": sum(worker_ram_values) / len(worker_ram_values)
                    if worker_ram_values
                    else 0,
                    "min_mb": min(worker_ram_values) if worker_ram_values else 0,
                    "max_observed_mb": max(worker_ram_values)
                    if worker_ram_values
                    else 0,
                },
            },
            "system_resources": {
                "vram_usage": {
                    "current_mb": system_vram_values[-1] if system_vram_values else 0,
                    "max_mb": max_system_vram,
                    "avg_mb": sum(system_vram_values) / len(system_vram_values)
                    if system_vram_values
                    else 0,
                    "min_mb": min(system_vram_values) if system_vram_values else 0,
                    "max_observed_mb": max(system_vram_values)
                    if system_vram_values
                    else 0,
                },
                "ram_usage": {
                    "current_mb": system_ram_values[-1] if system_ram_values else 0,
                    "max_mb": max_system_ram,
                    "avg_mb": sum(system_ram_values) / len(system_ram_values)
                    if system_ram_values
                    else 0,
                    "min_mb": min(system_ram_values) if system_ram_values else 0,
                    "max_observed_mb": max(system_ram_values)
                    if system_ram_values
                    else 0,
                },
            },
            "gpu_utilization": {
                "current_percent": gpu_util_values[-1] if gpu_util_values else 0,
                "avg_percent": sum(gpu_util_values) / len(gpu_util_values)
                if gpu_util_values
                else 0,
                "max_percent": max(gpu_util_values) if gpu_util_values else 0,
            },
            "worker_config": worker_config,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get resource stats for {model_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/resource-monitoring/status")
async def get_resource_monitoring_status(
    worker_controller=Depends(get_worker_controller),
):
    """
    Get resource monitoring system status.

    Returns:
        Dict containing monitoring system status
    """
    try:
        if not worker_controller:
            return {
                "monitoring_available": False,
                "message": "Worker controller not available",
            }

        if not hasattr(worker_controller, "resource_monitor_enabled"):
            return {
                "monitoring_available": False,
                "message": "Resource monitoring not configured",
            }

        if not worker_controller.resource_monitor_enabled:
            return {
                "monitoring_available": False,
                "message": "Resource monitoring not enabled",
            }

        # Get managed processes from process_ipc
        try:
            # Use the correct method to get managed processes
            status = worker_controller.get_all_process_info()
            managed_processes = list(status.keys()) if status else []
        except Exception:
            managed_processes = []

        return {
            "monitoring_available": True,
            "active_models": managed_processes,
            "monitoring_type": "process_ipc_native",
            "message": "Process IPC resource monitoring is active",
        }

    except Exception as e:
        logger.error(f"Failed to get monitoring status: {e}")
        raise HTTPException(status_code=500, detail=str(e))
