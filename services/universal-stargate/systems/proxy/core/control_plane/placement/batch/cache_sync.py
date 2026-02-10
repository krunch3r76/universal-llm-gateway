"""
Synchronous cache verification after eviction.

Ensures coordinator and WebSocket cache reflect eviction
before proceeding with new routing decisions.

Uses event-driven waiting (NOT polling) for non-blocking checks.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from gateways import GatewayInstance

    from ...model_lifecycle.coordination import GlobalModelLoadCoordinator

logger = get_logger(__name__)


async def verify_eviction_complete(
    model_id: str,
    gateway: GatewayInstance,
    coordinator: GlobalModelLoadCoordinator,
    timeout: float = 5.0,
    event_bus=None,
) -> bool:
    """
    Verify model eviction is reflected in coordinator and cache.

    Event-driven: waits for coordinator state change, not polling.

    Args:
        model_id: Evicted model ID
        gateway: Gateway where model was evicted
        coordinator: Global load coordinator
        timeout: Maximum wait time
        event_bus: EventBus instance for event subscription (optional)

    Returns:
        True if eviction confirmed, False if timeout
    """
    from src.scheduling.events import MODEL_UNLOADED

    gateway_name = gateway.config.name

    # Check current state immediately
    loaded_on = coordinator.where_is_loaded(model_id)
    if loaded_on != gateway_name:
        logger.debug(f"✅ Eviction already reflected: {model_id} not on {gateway_name}")
        return True

    # If no event bus available, check immediately and return
    if not event_bus:
        logger.debug("Event bus not available, checking state immediately")
        loaded_on = coordinator.where_is_loaded(model_id)
        return loaded_on != gateway_name

    # Create event for waiting on state change
    event = asyncio.Event()

    # Subscribe to model state changes (event-driven, not polling)
    def on_model_unload(ev):
        """Handler for model unload events."""
        if ev.payload.get("model_id") == model_id:
            event.set()

    try:
        event_bus.subscribe_async(MODEL_UNLOADED, on_model_unload)
    except Exception as e:
        logger.warning(f"Could not subscribe to model events: {e}")
        # Fallback: check immediately and return
        loaded_on = coordinator.where_is_loaded(model_id)
        return loaded_on != gateway_name

    try:
        # Wait for event with timeout
        await asyncio.wait_for(event.wait(), timeout=timeout)

        # Verify state after event
        loaded_on = coordinator.where_is_loaded(model_id)
        if loaded_on != gateway_name:
            logger.debug(
                f"✅ Eviction verified: {model_id} no longer on {gateway_name}"
            )
            return True

        logger.warning(
            f"⚠️ Event received but state unchanged: {model_id} still on {gateway_name}"
        )
        return False

    except TimeoutError:
        logger.warning(
            f"⚠️ Eviction verification timeout: {model_id} still on {gateway_name} "
            f"after {timeout}s"
        )
        return False
    finally:
        # Cleanup: unsubscribe
        try:
            event_bus.unsubscribe(MODEL_UNLOADED, on_model_unload)
        except Exception:
            pass


async def sync_cache_after_eviction(
    evicted_models: list[tuple[str, str]],  # (model_id, gateway_name)
    coordinator: GlobalModelLoadCoordinator,
    gateway_lookup: dict[str, GatewayInstance],
    timeout_per_model: float = 2.0,
    event_bus=None,
) -> int:
    """
    Synchronize cache after batch eviction.

    Args:
        evicted_models: List of (model_id, gateway_name) tuples
        coordinator: Global load coordinator
        gateway_lookup: Gateway instances by name
        timeout_per_model: Timeout per model verification
        event_bus: EventBus instance (optional)

    Returns:
        Number of models successfully verified
    """
    verified = 0

    for model_id, gateway_name in evicted_models:
        gateway = gateway_lookup.get(gateway_name)
        if not gateway:
            logger.warning(f"Gateway {gateway_name} not found for cache sync")
            continue

        if await verify_eviction_complete(
            model_id, gateway, coordinator, timeout_per_model, event_bus
        ):
            verified += 1

    logger.info(
        f"Cache sync complete: {verified}/{len(evicted_models)} evictions verified"
    )
    return verified
