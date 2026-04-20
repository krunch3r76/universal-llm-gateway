"""
Model event emitter - bridges WebSocket callbacks to EventBus.

Problem: WebSocket message handlers call callbacks directly but don't
publish events to the EventBus. Many consumers (ModelCacheConsumer,
ResourceVerifier, BatchModelTracker) subscribe to MODEL_LOADED events
that never arrive.

Solution: This module provides functions to emit model lifecycle events
to the EventBus, called from the WebSocket callback handlers.

Domain: Proxy
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

if TYPE_CHECKING:
    from universal_event_bus import EventBus

logger = get_logger(__name__)


async def emit_model_loaded(
    event_bus: EventBus,
    model_id: ModelId,
    gateway_url: str,
    gateway_name: str,
    vram_mb: int = 0,
    ram_mb: int = 0,
) -> None:
    """
    Emit MODEL_LOADED event to EventBus.

    Call this from WebSocket MODEL_LOADED callback handler.

    Args:
        event_bus: EventBus instance
        model_id: Model that finished loading
        gateway_url: Gateway HTTP URL (e.g., "http://localhost:9998")
        gateway_name: Gateway name (e.g., "gateway-1")
        vram_mb: VRAM used by model (optional, for observability)
        ram_mb: RAM used by model (optional, for observability)
    """
    from src.scheduling.events import ModelLoaded

    try:
        await event_bus.publish_nowait(
            ModelLoaded(
                url=gateway_url,
                model_id=str(model_id),
                gateway_name=gateway_name,
                vram_mb=vram_mb,
                ram_mb=ram_mb,
            )
        )
        logger.debug(f"Emitted MODEL_LOADED for {model_id} on {gateway_name}")
    except Exception as e:
        logger.warning(f"Failed to emit MODEL_LOADED for {model_id}: {e}")


async def emit_model_unloaded(
    event_bus: EventBus,
    model_id: ModelId,
    gateway_url: str,
    gateway_name: str,
) -> None:
    """
    Emit MODEL_UNLOADED event to EventBus.

    Call this from WebSocket MODEL_UNLOADED callback handler.

    Args:
        event_bus: EventBus instance
        model_id: Model that was unloaded
        gateway_url: Gateway HTTP URL
        gateway_name: Gateway name
    """
    from src.scheduling.events import ModelUnloaded

    try:
        await event_bus.publish_nowait(
            ModelUnloaded(
                url=gateway_url,
                model_id=str(model_id),
                gateway_name=gateway_name,
            )
        )
        logger.debug(f"Emitted MODEL_UNLOADED for {model_id} on {gateway_name}")
    except Exception as e:
        logger.warning(f"Failed to emit MODEL_UNLOADED for {model_id}: {e}")


async def emit_model_loading_failed(
    event_bus: EventBus,
    model_id: ModelId,
    gateway_url: str,
    gateway_name: str,
    error_message: str,
) -> None:
    """
    Emit MODEL_LOAD_FAILED event to EventBus.

    Call this from WebSocket MODEL_LOAD_FAILED callback handler.

    Args:
        event_bus: EventBus instance
        model_id: Model that failed to load
        gateway_url: Gateway HTTP URL
        gateway_name: Gateway name
        error_message: Error message from gateway
    """
    from src.scheduling.events import ModelLoadingFailed

    try:
        await event_bus.publish_nowait(
            ModelLoadingFailed(
                url=gateway_url,
                model_id=str(model_id),
                gateway_name=gateway_name,
                error=error_message,
            )
        )
        logger.debug(f"Emitted MODEL_LOAD_FAILED for {model_id} on {gateway_name}")
    except Exception as e:
        logger.warning(f"Failed to emit MODEL_LOAD_FAILED for {model_id}: {e}")
