"""Resource monitoring availability route reporting process IPC monitor status."""

from fastapi import Depends, HTTPException

from src.routers.dependencies import get_worker_controller

from .deps import logger, router


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

        try:
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

    except Exception as exc:
        logger.error(f"Failed to get monitoring status: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
