"""Scheduler status endpoint"""

from fastapi import APIRouter, Depends
from universal_logging import get_logger

from ...core.errors import RequestErrorBuilder
from ...dependencies import get_auth_dependency, get_proxy
from ...stargate_core import StargateProxy

logger = get_logger(__name__)
router = APIRouter(tags=["scheduler"])


@router.get("/status")
async def get_scheduler_status(
    proxy: StargateProxy = Depends(get_proxy),
    current_user: dict = Depends(get_auth_dependency),
):
    """Get current scheduler status"""
    try:
        # Scheduler components are always initialized when possible
        if proxy.request_queue:
            return proxy.request_queue.get_queue_stats()
        else:
            return {
                "error": "Scheduler unavailable (fallback to immediate execution mode)"
            }
    except Exception as e:
        logger.error(f"Error getting scheduler status: {e}")
        raise RequestErrorBuilder.internal_error(
            "Failed to get scheduler status", operation="get_scheduler_status"
        )
