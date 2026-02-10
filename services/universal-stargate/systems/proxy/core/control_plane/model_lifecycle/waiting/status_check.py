"""
HTTP status check fallback for model load waiting.

Used when WebSocket events may have been missed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

if TYPE_CHECKING:
    from gateways import GatewayInstance

logger = get_logger(__name__)


async def check_model_loaded_via_http(
    gateway: GatewayInstance | None,
    model_id: ModelId,
    gateway_name: str,
) -> bool:
    """
    Check if model is loaded via HTTP status API.

    Fallback for stale WebSocket cache. Handles cases where:
    - WebSocket connection was interrupted and missed MODEL_LOADED event
    - Stargate restarted after model was loaded
    - WebSocket cache has stale data

    Args:
        gateway: Gateway instance to check (may be None)
        model_id: Model ID to check
        gateway_name: Gateway name for logging

    Returns:
        True if model is confirmed loaded via HTTP, False otherwise
    """
    if gateway is None:
        return False

    try:
        from ..status import ModelStatus, get_model_status

        status = await get_model_status(gateway, str(model_id))
        if status.reachable and status.status in (
            ModelStatus.LOADED,
            ModelStatus.BUSY,
        ):
            return True
        return False
    except Exception as e:
        logger.debug(f"HTTP status check failed for {model_id} on {gateway_name}: {e}")
        return False
