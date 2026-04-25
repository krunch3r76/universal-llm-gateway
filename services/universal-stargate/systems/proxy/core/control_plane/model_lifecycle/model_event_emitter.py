"""
Model event emitter - bridges WebSocket callbacks to EventBus.

MODEL_LOADING_STARTED, MODEL_LOADED, and MODEL_LOAD_FAILED are emitted
directly from the WebSocket message handlers via EventPublisher
(gateway_websocket/ws_client/events.py) — that path is decoupled from the
lifecycle-callback chain that was prone to being overwritten by
federation/manager wiring.

This module retains only emit_model_unloaded, used by the unloaded-callback
wrapper in waiting/websocket_callbacks.py.

Domain: Proxy
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

if TYPE_CHECKING:
    from universal_event_bus import EventBus

logger = get_logger(__name__)


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
