"""
WebSocket event handlers and sync methods for GlobalModelLoadCoordinator.

Handles MODEL_LOADED, MODEL_UNLOADED, and other gateway WebSocket events.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

if TYPE_CHECKING:
    from .global_coordinator import GlobalModelLoadCoordinator

logger = get_logger(__name__)


async def mark_as_loading_from_event(
    coordinator: GlobalModelLoadCoordinator,
    model: ModelId,
    gateway: str,
) -> None:
    """
    Mark model as loading from WebSocket event.

    Only updates if not already tracked via request_model_load().
    This ensures WebSocket events keep coordinator in sync even for
    loads initiated outside the coordinator (e.g., direct gateway API calls).
    """
    routing_key = model.routing_key
    # Only set if not already tracked (don't overwrite existing event)
    if routing_key not in coordinator._models_loading:
        # Create event for waiters (in case anyone wants to wait)
        event = asyncio.Event()
        coordinator._models_loading[routing_key] = (gateway, event)
    # Clear any stale error state when load starts
    coordinator._models_error.pop(routing_key, None)


async def mark_as_loaded(
    coordinator: GlobalModelLoadCoordinator,
    model: ModelId,
    gateway: str,
) -> None:
    """Mark model as loaded in coordinator state."""
    routing_key = model.routing_key
    coordinator._models_loaded[routing_key] = gateway
    logger.debug(f"✅ Marked {routing_key} as loaded on {gateway}")

    # Clear loading state and signal waiters
    loading_entry = coordinator._models_loading.pop(routing_key, None)
    if loading_entry:
        _, event = loading_entry
        event.set()
        # This was a coordinated load - mark as verified
        # Store by canonical model_id (not routing_key) to distinguish context variants
        canonical_id = str(model)
        coordinator._coordinator_verified[canonical_id] = time.monotonic()
    # Clear error state on successful load
    coordinator._models_error.pop(routing_key, None)


async def mark_as_unloaded(
    coordinator: GlobalModelLoadCoordinator,
    model: ModelId,
) -> None:
    """Mark model as unloaded in coordinator state."""
    routing_key = model.routing_key
    coordinator._models_loaded.pop(routing_key, None)
    coordinator._models_loading.pop(routing_key, None)
    coordinator._models_error.pop(routing_key, None)
    # Clear coordinator verification (use canonical model_id)
    canonical_id = str(model)
    coordinator._coordinator_verified.pop(canonical_id, None)
    logger.debug(f"🗑️ Marked {routing_key} as unloaded")

    # Note: MODEL_UNLOADED events are emitted by WebSocket callbacks
    # (websocket_callbacks.py::_emit_model_unloaded_event) which have
    # access to the event bus instance. This function only updates
    # coordinator internal state.


async def mark_as_error(
    coordinator: GlobalModelLoadCoordinator,
    model: ModelId,
    gateway: str,
    error_message: str,
) -> None:
    """Mark model as in error state."""
    routing_key = model.routing_key
    coordinator._models_error[routing_key] = (gateway, error_message)
    # Clear loading state and signal waiters
    loading_entry = coordinator._models_loading.pop(routing_key, None)
    if loading_entry:
        _, event = loading_entry
        event.set()  # Wake any waiters (they'll see error state)
    # Clear loaded state (can't be both loaded and error)
    coordinator._models_loaded.pop(routing_key, None)
    # Clear coordinator verification (error state invalidates verification)
    # Use canonical model_id to match how it was stored
    canonical_id = str(model)
    coordinator._coordinator_verified.pop(canonical_id, None)


async def sync_loaded_models(
    coordinator: GlobalModelLoadCoordinator,
    gateway_name: str,
    loaded_models: frozenset[str],
) -> None:
    """
    Sync coordinator state with gateway's currently loaded models.

    Called on WebSocket connect/reconnect to ensure coordinator
    knows about models loaded before Stargate started or during
    WebSocket disconnection.

    Pre: gateway_name is connected
    Post: ∀ routing_key: coordinator._models_loaded[routing_key] = gateway_name ⟺
          ∃ model_id ∈ loaded_models:
              ModelId.parse(model_id).routing_key = routing_key
    """
    # Build set of routing keys from gateway's loaded models
    gateway_routing_keys: set[str] = set()
    for model_id in loaded_models:
        routing_key = coordinator._get_routing_key(model_id)
        gateway_routing_keys.add(routing_key)

    removed_count = 0
    synced_count = 0

    # Remove stale entries for this gateway
    for routing_key, tracked_gateway in list(coordinator._models_loaded.items()):
        if tracked_gateway != gateway_name:
            continue
        if routing_key not in gateway_routing_keys:
            del coordinator._models_loaded[routing_key]
            # Clear coordinator verification for stale entries
            # Need to find canonical model_ids with this routing_key
            for model_id in list(coordinator._coordinator_verified.keys()):
                try:
                    model_routing_key = ModelId.parse(model_id).routing_key
                except ValueError:
                    # Fallback for unparseable IDs
                    model_routing_key = coordinator._get_routing_key(model_id)

                if model_routing_key == routing_key:
                    coordinator._coordinator_verified.pop(model_id, None)
            removed_count += 1
            logger.debug(
                f"🗑️ Removed stale entry: {routing_key} no longer on {gateway_name}"
            )

    # Add new entries
    for routing_key in gateway_routing_keys:
        if routing_key not in coordinator._models_loaded:
            coordinator._models_loaded[routing_key] = gateway_name
            synced_count += 1
            logger.debug(f"📍 Synced: {routing_key} loaded on {gateway_name}")

    if synced_count > 0 or removed_count > 0:
        logger.info(
            f"🔄 Synced {synced_count} loaded, {removed_count} removed "
            f"from {gateway_name} to coordinator"
        )


async def clear_verified_state_for_gateway(
    coordinator: GlobalModelLoadCoordinator,
    gateway_name: str,
) -> None:
    """
    Clear coordinator-verified state for a gateway on reconnection.

    Called when WebSocket reconnects to ensure we don't trust stale
    coordinator-verified state (may have missed events during disconnection).

    Pre: WebSocket reconnecting/reconnected
    Post: ∀ routing_key where _models_loaded[routing_key] = gateway_name:
          _coordinator_verified[routing_key] = None (cleared)
    """
    # Find routing keys loaded on this gateway
    routing_keys_on_gateway = {
        routing_key
        for routing_key, gateway in coordinator._models_loaded.items()
        if gateway == gateway_name
    }

    # Clear coordinator-verified entries for matching models
    # _coordinator_verified is keyed by canonical model_id, so we parse
    # to get routing_key
    cleared_count = 0
    for model_id in list(coordinator._coordinator_verified.keys()):
        try:
            model_routing_key = ModelId.parse(model_id).routing_key
        except ValueError:
            # Fallback: use _get_routing_key for unparseable IDs
            model_routing_key = coordinator._get_routing_key(model_id)

        if model_routing_key in routing_keys_on_gateway:
            coordinator._coordinator_verified.pop(model_id, None)
            cleared_count += 1

    if cleared_count > 0:
        logger.debug(
            "🔄 Cleared coordinator-verified state for %d model(s) on %s "
            "(WebSocket reconnection)",
            cleared_count,
            gateway_name,
        )


def was_load_coordinated(
    coordinator: GlobalModelLoadCoordinator,
    model_id: ModelId,
    max_age: float = 600.0,
) -> bool:
    """
    Check if a model load was coordinated by this instance.

    Returns True if we received MODEL_LOADED event for a load we initiated
    and the verification is recent (within max_age seconds).

    Args:
        coordinator: GlobalModelLoadCoordinator instance
        model_id: Model ID to check (uses full ID to distinguish context variants)
        max_age: Maximum age of verification in seconds (default 600s)

    Returns:
        True if load was coordinated by this instance and verification is recent
    """
    # Use routing key for consistent comparison
    canonical_id = str(model_id)

    verified_at = coordinator._coordinator_verified.get(canonical_id)
    if verified_at is None:
        return False
    age = time.monotonic() - verified_at
    return age <= max_age
