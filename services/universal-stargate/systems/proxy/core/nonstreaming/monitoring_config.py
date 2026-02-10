"""
Monitoring configuration helpers.

Non-blocking cache access and background fetch scheduling for model configuration.
Used by request handlers to get monitoring metadata without blocking.

Invariants:
- ∀ model_id: type(model_id) = ModelId (parsed at boundary)
- ∀ background_fetch: fire_and_forget ∧ exception_swallowed
- ∀ cache_access: synchronous (no await on request path)
"""

import asyncio
from typing import TYPE_CHECKING, Any

from model_id import ModelId
from universal_logging import get_logger

from ...utils.model_metadata_helpers import metadata_to_monitoring_dict

if TYPE_CHECKING:
    from gateways.single_manager import SingleGatewayManager

logger = get_logger(__name__)


def get_cached_configuration_for_monitoring(
    gateway_manager: "SingleGatewayManager",
    model_id: ModelId,
) -> dict[str, Any]:
    """
    Get cached model configuration as monitoring dict.

    Synchronous cache lookup - never blocks request path.
    Uses ModelId comparison semantics for cache lookup.

    Args:
        gateway_manager: Gateway manager with configuration cache
        model_id: Model ID object (normalized internally)

    Returns:
        Monitoring dict with format/input_schema/context_length/etc if cached,
        empty dict if not cached

    Invariant: ∀ call: await_count = 0 (synchronous)
    """
    cached = gateway_manager.get_cached_model_configuration(model_id)
    if cached:
        # For monitoring dict, use string representation
        return metadata_to_monitoring_dict(cached, str(model_id))
    return {}


def schedule_background_configuration_fetch(
    gateway_manager: "SingleGatewayManager",
    model_id: ModelId,
) -> None:
    """
    Schedule background fetch for model configuration (fire-and-forget).

    Creates async task to fetch configuration for future requests.
    Task swallows exceptions to avoid "Task exception was never retrieved".

    Args:
        gateway_manager: Gateway manager to fetch through
        model_id: Model ID object

    Invariant: ∀ call: blocks_request_path = False
    """
    _ = asyncio.create_task(_fetch_configuration_background(gateway_manager, model_id))


async def _fetch_configuration_background(
    gateway_manager: "SingleGatewayManager",
    model_id: ModelId,
) -> None:
    """
    Background task to fetch and cache model configuration.

    Fire-and-forget - outcomes logged with structured metadata.

    Args:
        gateway_manager: Gateway manager to fetch through
        model_id: Model ID object

    Returns:
        None
    """
    try:
        config = await gateway_manager.fetch_model_configuration(model_id)
        if config:
            logger.info(
                f"Background configuration fetch completed for {model_id}",
                extra={
                    "model_id": str(model_id),  # Convert ModelId to string for logging
                    "success": True,
                    "phase": "background_fetch",
                },
            )
        else:
            logger.warning(
                f"Background configuration fetch returned None for {model_id}",
                extra={
                    "model_id": str(model_id),
                    "success": False,
                    "reason": "not_found",
                },
            )
    except Exception as e:
        logger.warning(
            f"Background configuration fetch failed for {model_id}: {e}",
            extra={
                "model_id": str(model_id),
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )
