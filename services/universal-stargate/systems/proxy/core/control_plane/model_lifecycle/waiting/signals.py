"""
Signal handlers for model load/unload events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

from .handles import LoadWaitHandle, UnloadWaitHandle

if TYPE_CHECKING:
    from ..coordination import GlobalModelLoadCoordinator

logger = get_logger(__name__)


async def signal_loaded(
    pending_loads: dict[tuple[str, str], LoadWaitHandle],
    global_coordinator: GlobalModelLoadCoordinator | None,
    gateway_name: str,
    model_id: ModelId,
    event_key: str,
) -> None:
    """Signal that a model finished loading (from WebSocket callback)."""
    logger.debug(f"🔍 _signal_loaded: gateway={gateway_name}, model_id={model_id}")
    logger.debug(f"🔍 event_key={event_key}, pending_loads={list(pending_loads)}")

    # Notify global coordinator (with original model_id for proper parsing)
    if global_coordinator:
        global_coordinator.on_model_loaded_event(model_id, gateway_name)
        logger.info(
            f"🔔 MODEL_LOADED via WebSocket: {model_id} → {event_key} "
            f"on {gateway_name} → coordinator updated"
        )

    # Wake load waiters (with normalized event_key)
    wake_load_waiters(pending_loads, gateway_name, event_key, loaded=True)


async def signal_unloaded(
    pending_unloads: dict[tuple[str, str], UnloadWaitHandle],
    global_coordinator: GlobalModelLoadCoordinator | None,
    gateway_name: str,
    model_id: ModelId,
    event_key: str,
) -> None:
    """Signal that a model finished unloading (from WebSocket callback)."""
    # Notify global coordinator (with original model_id for proper parsing)
    if global_coordinator:
        global_coordinator.on_model_unloaded_event(model_id)
        logger.info(
            f"🔔 MODEL_UNLOADED via WebSocket: {model_id} → {event_key} "
            f"on {gateway_name} → coordinator updated"
        )

    # Wake unload waiters (with normalized event_key)
    wake_unload_waiters(pending_unloads, gateway_name, event_key)


async def signal_load_failed(
    pending_loads: dict[tuple[str, str], LoadWaitHandle],
    gateway_name: str,
    model_id: ModelId,
    event_key: str,
    error: str,
) -> None:
    """Signal that a model load failed."""
    # Wake load waiters with failure (using normalized event_key)
    handle = pending_loads.get((gateway_name, event_key))
    if handle:
        logger.debug(f"Signaling load failure for {model_id} → {event_key}: {error}")
        handle.set_failed(error)


def wake_load_waiters(
    pending_loads: dict[tuple[str, str], LoadWaitHandle],
    gateway_name: str,
    event_key: str,
    loaded: bool,
) -> None:
    """Wake all load waiters for this model (event_key already normalized)."""
    handle = pending_loads.get((gateway_name, event_key))
    if handle:
        logger.debug(f"Waking {handle.waiter_count} load waiter(s) for {event_key}")
        if loaded:
            handle.set_loaded()
        else:
            handle.set_failed("Unloaded during load")


def wake_unload_waiters(
    pending_unloads: dict[tuple[str, str], UnloadWaitHandle],
    gateway_name: str,
    event_key: str,
) -> None:
    """Wake all unload waiters for this model (event_key already normalized)."""
    handle = pending_unloads.get((gateway_name, event_key))
    if handle:
        logger.debug(f"Waking {handle.waiter_count} unload waiter(s) for {event_key}")
        handle.set_unloaded()


def notify_gateway_disconnected(
    pending_loads: dict[tuple[str, str], LoadWaitHandle],
    pending_unloads: dict[tuple[str, str], UnloadWaitHandle],
    gateway_name: str,
) -> None:
    """Handle gateway WebSocket disconnect - fail all pending waits."""
    # Fail load waits
    load_keys_to_remove = [key for key in pending_loads if key[0] == gateway_name]
    for key in load_keys_to_remove:
        handle = pending_loads.pop(key)
        handle.set_unreachable()

    # Fail unload waits
    unload_keys_to_remove = [key for key in pending_unloads if key[0] == gateway_name]
    for key in unload_keys_to_remove:
        handle = pending_unloads.pop(key)
        handle.set_unreachable()

    removed_count = len(load_keys_to_remove) + len(unload_keys_to_remove)
    if removed_count > 0:
        logger.warning(
            f"Gateway {gateway_name} disconnected, failed {removed_count} pending waits"
        )
