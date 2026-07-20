"""WebSocketEventForwarder — subscribes to EventBus and broadcasts to Stargate clients.

Lifecycle, event dispatch, and broadcast logging live here; payload-to-message
conversion is delegated to the message_builders module.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ...events import Event, EventBus
from ...events.types import (
    CATALOG_RELOADED,
    COMPUTE_CAPACITY_QUEUE_ACQUIRED,
    COMPUTE_CAPACITY_QUEUE_WAIT,
    GATEWAY_DRAINING,
    GATEWAY_SHUTDOWN,
    INFERENCE_COMPLETED,
    INFERENCE_STARTED,
    MODEL_LOAD_FAILED,
    MODEL_LOADED,
    MODEL_LOADING_PROGRESS,
    MODEL_LOADING_STARTED,
    MODEL_UNLOADED,
    PHANTOM_MODEL_CLEANED,
    PHANTOM_MODEL_DETECTED,
    REQUEST_INFERENCE_STARTED,
    SYSTEM_RESOURCES_UPDATED,
    VRAM_ORPHAN_DETECTED,
    VRAM_STALENESS_DETECTED,
)
from ..connection_manager import StargateConnectionManager
from ..messages import WebSocketMessage
from .message_builders import build_async_message, build_sync_message

if TYPE_CHECKING:
    from ..init_cache import InitDataCache

logger = get_logger(__name__)


class WebSocketEventForwarder:
    """
    Forward gateway EventBus signals to Stargate WebSocket clients.

    Responsibilities:
    - Subscribe to the gateway event bus for selected signals.
    - Convert events into wire-format WebSocket messages.
    - Broadcast converted messages to connected Stargate clients.
    - Keep forwarding non-blocking while surfacing malformed payloads and
      unexpected runtime failures through structured logs.
    """

    FORWARDED_EVENTS = [
        MODEL_LOADING_STARTED,
        MODEL_LOADING_PROGRESS,
        MODEL_LOADED,
        MODEL_LOAD_FAILED,
        MODEL_UNLOADED,
        INFERENCE_STARTED,
        INFERENCE_COMPLETED,
        REQUEST_INFERENCE_STARTED,
        SYSTEM_RESOURCES_UPDATED,
        CATALOG_RELOADED,
        GATEWAY_SHUTDOWN,
        GATEWAY_DRAINING,
        COMPUTE_CAPACITY_QUEUE_WAIT,
        COMPUTE_CAPACITY_QUEUE_ACQUIRED,
        VRAM_ORPHAN_DETECTED,
        VRAM_STALENESS_DETECTED,
        PHANTOM_MODEL_DETECTED,
        PHANTOM_MODEL_CLEANED,
    ]

    def __init__(
        self,
        event_bus: EventBus,
        connection_manager: StargateConnectionManager,
        init_cache: InitDataCache | None = None,
    ):
        self._event_bus = event_bus
        self._connection_manager = connection_manager
        self._init_cache = init_cache
        self._subscribed = False
        self._pending_tasks: set[asyncio.Task[Any]] = set()

    def start(self) -> None:
        """Subscribe to FORWARDED_EVENTS on the event bus."""
        if self._subscribed:
            return

        for event_type in self.FORWARDED_EVENTS:
            self._event_bus.subscribe_async(event_type, self._handle_event)

        self._subscribed = True
        event_count = len(self.FORWARDED_EVENTS)
        logger.info(f"WebSocketEventForwarder subscribed to {event_count} event types")

    def stop(self) -> None:
        """Stop forwarding events; handlers remain subscribed but skip processing."""
        if not self._subscribed:
            return

        self._subscribed = False
        logger.info("WebSocketEventForwarder stopped")

    async def _handle_event(self, event: Event) -> None:
        """Handle EventBus event and forward to WebSocket clients."""
        if not self._subscribed:
            return

        task = asyncio.create_task(self._process_and_broadcast(event))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _process_and_broadcast(self, event: Event) -> None:
        """Convert event to message and broadcast."""
        try:
            if event.signal == MODEL_LOADED:
                logger.info(
                    "🔔 Processing MODEL_LOADED event: "
                    f"model_id={event.payload.get('model_id')}"
                )

            message = await self._event_to_message(event)
            if message:
                await self._broadcast_with_logging(message, event.signal)
        except (KeyError, TypeError, ValueError) as e:
            logger.error(
                "Failed to process malformed event %s: %s",
                event.signal,
                e,
                exc_info=True,
            )
        except Exception:
            logger.exception("Unexpected failure processing event %s", event.signal)

    async def _broadcast_with_logging(
        self, message: WebSocketMessage, signal: str
    ) -> None:
        """Broadcast message and log success/failure."""
        try:
            count = await self._connection_manager.broadcast(message)

            if signal == MODEL_LOADED:
                if count > 0:
                    logger.info(
                        f"✅ MODEL_LOADED broadcast to {count} client(s): "
                        f"model_id={message.data.get('model_id')}"
                    )
                else:
                    logger.error(
                        "❌ MODEL_LOADED broadcast failed - no clients connected: "
                        f"model_id={message.data.get('model_id')}"
                    )
            elif signal == CATALOG_RELOADED:
                model_count = (
                    len(message.data.get("models", []))
                    if message.data.get("models")
                    else 0
                )
                if count > 0:
                    logger.info(
                        f"✅ CATALOG_UPDATE broadcast to {count} client(s): "
                        f"{model_count} models, reason={message.data.get('reason')}"
                    )
                else:
                    logger.warning(
                        f"⚠️ CATALOG_UPDATE not sent - no clients connected: "
                        f"{model_count} models available"
                    )
            elif signal in (INFERENCE_STARTED, INFERENCE_COMPLETED):
                model_id = message.data.get("model_id", "?")
                label = "MODEL_BUSY" if signal == INFERENCE_STARTED else "MODEL_IDLE"
                if count > 0:
                    logger.info(
                        f"📡 {label} broadcast to {count} client(s): "
                        f"model_id={model_id}"
                    )
                else:
                    logger.warning(
                        f"⚠️ {label} NOT sent — no clients connected: "
                        f"model_id={model_id}"
                    )
        except Exception as e:
            logger.error(
                "WebSocket broadcast failed for %s: %s",
                signal,
                e,
                exc_info=True,
            )
            raise

    async def _event_to_message(self, event: Event) -> WebSocketMessage | None:
        """Convert EventBus event to WebSocket message."""
        payload = event.payload
        if not isinstance(payload, dict):
            logger.error(
                "Invalid event payload type in forwarder: signal=%s type=%s",
                event.signal,
                type(payload).__name__,
            )
            return None

        if message := build_sync_message(event.signal, payload):
            return message
        return await build_async_message(event.signal, payload, self._init_cache)
