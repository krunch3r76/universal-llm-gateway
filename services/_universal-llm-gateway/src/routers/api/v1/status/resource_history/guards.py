"""Guard helpers that verify process IPC resource monitoring is enabled.

Centralizes the repeated availability checks performed before history, stats,
and monitoring status routes query worker controller resource telemetry.
"""

from fastapi import HTTPException


def require_resource_monitor(worker_controller) -> None:
    """Raise HTTP 503 when resource monitoring is unavailable or disabled."""
    if not worker_controller or not hasattr(
        worker_controller, "resource_monitor_enabled"
    ):
        raise HTTPException(status_code=503, detail="Resource monitoring not available")

    if not worker_controller.resource_monitor_enabled:
        raise HTTPException(status_code=503, detail="Resource monitoring not enabled")
