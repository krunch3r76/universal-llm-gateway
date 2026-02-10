"""
Hot reload API endpoints for configuration management.

Provides endpoints for manual configuration reload and status monitoring.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from universal_logging import get_logger

try:
    from ....core.hot_reload import HotReloadManager, ReloadEvent
    from ....dependencies import get_hot_reload_manager
except ImportError:
    from src.core.hot_reload import HotReloadManager
    from src.routers.dependencies import get_hot_reload_manager

logger = get_logger(__name__)

router = APIRouter()


class ReloadRequest(BaseModel):
    """Request model for manual configuration reload"""

    file_path: str


class ReloadResponse(BaseModel):
    """Response model for configuration reload operations"""

    status: str
    message: str
    file_path: str
    model_key: str | None = None
    success: bool
    timestamp: datetime
    duration_ms: float | None = None
    error: str | None = None


class ReloadStatusResponse(BaseModel):
    """Response model for hot reload status"""

    enabled: bool
    watch_directory: str
    last_reload: datetime | None
    observer_running: bool
    error_count: int
    recent_changes: list[dict[str, Any]]


@router.post("/reload", response_model=ReloadResponse)
async def reload_config_file(
    request: ReloadRequest,
    hot_reload_manager: HotReloadManager = Depends(get_hot_reload_manager),
) -> ReloadResponse:
    """
    Manually reload a specific configuration file.

    Args:
        request: Reload request containing file path
        hot_reload_manager: Hot reload manager instance

    Returns:
        ReloadResponse with the result of the reload operation

    Raises:
        HTTPException: If hot reload is not enabled or file reload fails
    """
    if not hot_reload_manager.enabled:
        raise HTTPException(status_code=400, detail="Hot reload is not enabled")

    # Security validation
    from pathlib import Path

    file_path_obj = Path(request.file_path)

    # Check if path is in allowed directories
    try:
        hot_reload_manager._validate_watch_directory(file_path_obj)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=f"Access denied: {str(e)}")

    # Check file size
    try:
        hot_reload_manager._validate_file_size(file_path_obj)
    except ValueError as e:
        raise HTTPException(status_code=413, detail=f"File too large: {str(e)}")

    try:
        # Trigger reload
        event = await hot_reload_manager.reload_config_file(request.file_path)

        # Convert event to response
        response = ReloadResponse(
            status="success" if event.success else "error",
            message=f"Reloaded {request.file_path}"
            if event.success
            else f"Failed to reload {request.file_path}",
            file_path=event.file_path,
            model_key=event.model_key,
            success=event.success,
            timestamp=event.timestamp,
            duration_ms=event.duration_ms,
            error=event.error,
        )

        return response

    except Exception as e:
        logger.error(f"Error in reload endpoint: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/status", response_model=ReloadStatusResponse)
async def get_reload_status(
    hot_reload_manager: HotReloadManager = Depends(get_hot_reload_manager),
) -> ReloadStatusResponse:
    """
    Get hot reload status and recent changes.

    Args:
        hot_reload_manager: Hot reload manager instance

    Returns:
        ReloadStatusResponse with current status information
    """
    try:
        status = hot_reload_manager.get_status()

        # Convert recent changes to dictionaries
        recent_changes = []
        for event in status.recent_changes:
            recent_changes.append(
                {
                    "file_path": event.file_path,
                    "model_key": event.model_key,
                    "success": event.success,
                    "timestamp": event.timestamp.isoformat(),
                    "duration_ms": event.duration_ms,
                    "error": event.error,
                }
            )

        response = ReloadStatusResponse(
            enabled=status.enabled,
            watch_directory=status.watch_directory,
            last_reload=status.last_reload,
            observer_running=status.observer_running,
            error_count=status.error_count,
            recent_changes=recent_changes,
        )

        return response

    except Exception as e:
        logger.error(f"Error in status endpoint: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/start")
async def start_hot_reload(
    hot_reload_manager: HotReloadManager = Depends(get_hot_reload_manager),
) -> dict[str, Any]:
    """
    Start hot reload monitoring.

    Args:
        hot_reload_manager: Hot reload manager instance

    Returns:
        Dictionary with start result

    Raises:
        HTTPException: If start fails
    """
    try:
        success = await hot_reload_manager.start()

        if success:
            return {
                "status": "success",
                "message": "Hot reload started successfully",
                "enabled": hot_reload_manager.enabled,
            }
        else:
            raise HTTPException(status_code=400, detail="Failed to start hot reload")

    except Exception as e:
        logger.error(f"Error starting hot reload: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/stop")
async def stop_hot_reload(
    hot_reload_manager: HotReloadManager = Depends(get_hot_reload_manager),
) -> dict[str, Any]:
    """
    Stop hot reload monitoring.

    Args:
        hot_reload_manager: Hot reload manager instance

    Returns:
        Dictionary with stop result
    """
    try:
        await hot_reload_manager.stop()

        return {
            "status": "success",
            "message": "Hot reload stopped successfully",
            "enabled": hot_reload_manager.enabled,
        }

    except Exception as e:
        logger.error(f"Error stopping hot reload: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/recent-changes")
async def get_recent_changes(
    limit: int = Query(
        10, ge=1, le=100, description="Maximum number of recent changes to return"
    ),
    hot_reload_manager: HotReloadManager = Depends(get_hot_reload_manager),
) -> dict[str, Any]:
    """
    Get recent configuration changes.

    Args:
        limit: Maximum number of changes to return
        hot_reload_manager: Hot reload manager instance

    Returns:
        Dictionary with recent changes
    """
    try:
        status = hot_reload_manager.get_status()

        # Limit recent changes
        recent_changes = status.recent_changes[-limit:]

        # Convert to dictionaries
        changes = []
        for event in recent_changes:
            changes.append(
                {
                    "file_path": event.file_path,
                    "model_key": event.model_key,
                    "success": event.success,
                    "timestamp": event.timestamp.isoformat(),
                    "duration_ms": event.duration_ms,
                    "error": event.error,
                }
            )

        return {
            "status": "success",
            "changes": changes,
            "total_changes": len(status.recent_changes),
            "returned": len(changes),
        }

    except Exception as e:
        logger.error(f"Error getting recent changes: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
