"""Forward EventBus events to WebSocket clients."""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

from ..events import Event, EventBus
from ..events.types import (
    CATALOG_RELOADED,
    COMPUTE_CAPACITY_QUEUE_ACQUIRED,
    COMPUTE_CAPACITY_QUEUE_WAIT,
    GATEWAY_DRAINING,
    GATEWAY_SHUTDOWN,
    INFERENCE_COMPLETED,
    INFERENCE_STARTED,
    MODEL_LOAD_FAILED,
    MODEL_LOADED,
    MODEL_LOADING_STARTED,
    MODEL_UNLOADED,
    SYSTEM_RESOURCES_UPDATED,
)
from .connection_manager import StargateConnectionManager
from .messages import (
    WebSocketMessage,
    create_catalog_update_message,
    create_compute_queue_acquired_message,
    create_compute_queue_wait_message,
    create_gateway_draining_message,
    create_gateway_shutdown_message,
    create_model_busy_message,
    create_model_idle_message,
    create_model_load_failed_message,
    create_model_loaded_message,
    create_model_loading_started_message,
    create_model_unloaded_message,
    create_resource_update_message,
)

if TYPE_CHECKING:
    from .init_cache import InitDataCache

logger = get_logger(__name__)


class WebSocketEventForwarder:
    """Forwards EventBus events to WebSocket clients."""

    # Events to forward to Stargate
    FORWARDED_EVENTS = [
        MODEL_LOADING_STARTED,
        MODEL_LOADED,
        MODEL_LOAD_FAILED,
        MODEL_UNLOADED,
        INFERENCE_STARTED,
        INFERENCE_COMPLETED,
        SYSTEM_RESOURCES_UPDATED,
        CATALOG_RELOADED,
        GATEWAY_SHUTDOWN,
        GATEWAY_DRAINING,
        # Compute capacity telemetry (orchestration observability)
        COMPUTE_CAPACITY_QUEUE_WAIT,
        COMPUTE_CAPACITY_QUEUE_ACQUIRED,
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

    def start(self) -> None:
        """Subscribe to relevant events."""
        if self._subscribed:
            return

        for event_type in self.FORWARDED_EVENTS:
            self._event_bus.subscribe_async(event_type, self._handle_event)

        self._subscribed = True
        event_count = len(self.FORWARDED_EVENTS)
        logger.info(f"WebSocketEventForwarder subscribed to {event_count} event types")

    def stop(self) -> None:
        """Stop forwarding events.

        Note: Handlers remain subscribed to EventBus (by design).
        This method sets a flag that handlers check to skip processing.
        """
        if not self._subscribed:
            return

        # Set flag - handler checks this to skip processing
        self._subscribed = False
        logger.info("WebSocketEventForwarder stopped")

    async def _handle_event(self, event: Event) -> None:
        """
        Handle EventBus event and forward to WebSocket clients.

        Fire-and-forget: broadcasts asynchronously without blocking the event bus.
        Errors are logged but don't propagate to prevent event handler failures.
        """
        import asyncio

        # Skip if stopped (handlers can't be unsubscribed in EventBus v0.2.0+)
        if not self._subscribed:
            return

        # Create task for async message conversion and broadcast
        asyncio.create_task(self._process_and_broadcast(event))

    async def _process_and_broadcast(self, event: Event) -> None:
        """Convert event to message and broadcast."""
        try:
            # Log critical events explicitly
            if event.signal == MODEL_LOADED:
                logger.info(
                    "🔔 Processing MODEL_LOADED event: "
                    f"model_id={event.payload.get('model_id')}"
                )

            message = await self._event_to_message(event)
            if message:
                await self._broadcast_with_logging(message, event.signal)
        except Exception as e:
            logger.error(f"Failed to process event {event.signal}: {e}")

    async def _broadcast_with_logging(
        self, message: WebSocketMessage, signal: str
    ) -> None:
        """Broadcast message and log success/failure."""
        try:
            count = await self._connection_manager.broadcast(message)

            # Always log for critical events, regardless of count
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
                # Log catalog updates explicitly
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
            elif count > 0:
                # logger.debug(f"Forwarded {signal} to {count} WebSocket client(s)")
                pass
        except Exception as e:
            logger.error(f"WebSocket broadcast failed for {signal}: {e}")

    async def _event_to_message(self, event: Event) -> WebSocketMessage | None:
        """Convert EventBus event to WebSocket message."""
        payload = event.payload

        if event.signal == MODEL_LOADING_STARTED:
            return create_model_loading_started_message(
                model_id=payload.get("model_id", "unknown")
            )

        elif event.signal == MODEL_LOADED:
            return create_model_loaded_message(
                model_id=payload.get("model_id", "unknown"),
                vram_mb=payload.get("vram_usage_mb", 0),
                ram_mb=payload.get("ram_usage_mb", 0),
                context_length=payload.get("context_length"),
            )

        elif event.signal == MODEL_LOAD_FAILED:
            return create_model_load_failed_message(
                model_id=payload.get("model_id", "unknown"),
                error_message=payload.get("error_message", "Unknown error"),
            )

        elif event.signal == MODEL_UNLOADED:
            return create_model_unloaded_message(
                model_id=payload.get("model_id", "unknown")
            )

        elif event.signal == INFERENCE_STARTED:
            return create_model_busy_message(
                model_id=payload.get("model_id", "unknown")
            )

        elif event.signal == INFERENCE_COMPLETED:
            return create_model_idle_message(
                model_id=payload.get("model_id", "unknown"),
                last_inference_time=payload.get("last_inference_time", 0.0),
            )

        elif event.signal == SYSTEM_RESOURCES_UPDATED:
            logger.info(
                f"📡 Forwarding SYSTEM_RESOURCES_UPDATED to Stargate: "
                f"available_vram={payload.get('available_vram_mb', 0)}MB, "
                f"available_ram={payload.get('available_ram_mb', 0)}MB, "
                f"loaded_models={payload.get('loaded_models', [])}"
            )
            return create_resource_update_message(
                available_vram_mb=payload.get("available_vram_mb", 0),
                available_ram_mb=payload.get("available_ram_mb", 0),
                total_vram_mb=payload.get("total_vram_mb"),
                total_ram_mb=payload.get("total_ram_mb"),
                loaded_models=payload.get("loaded_models"),
                model_vram=payload.get("model_vram"),
            )

        elif event.signal == CATALOG_RELOADED:
            # Catalog update with fresh models list for Stargate
            reason = payload.get("reason", "reload")
            models = None
            catalog = None

            if self._init_cache:
                # Get fresh data from init_cache (now async)
                init_data = await self._init_cache.get_init_data()
                models = init_data.get("models", [])
                catalog = init_data.get("catalog", {})
                logger.info(
                    f"🔔 Creating CATALOG_UPDATE message: "
                    f"{len(models) if models else 0} models, reason={reason}"
                )
            else:
                logger.warning(
                    "⚠️ CATALOG_RELOADED but init_cache is None - cannot send model list"
                )

            return create_catalog_update_message(
                reason=reason,
                models=models,
                catalog=catalog,
            )

        elif event.signal == GATEWAY_SHUTDOWN:
            return create_gateway_shutdown_message(
                gateway_id=payload.get("gateway_id", "unknown"),
                reason=payload.get("reason", "unknown"),
                timestamp=payload.get("timestamp", 0),
            )

        elif event.signal == GATEWAY_DRAINING:
            return create_gateway_draining_message(
                gateway_id=payload.get("gateway_id", "unknown"),
                reason=payload.get("reason", "unknown"),
                timeout=payload.get("timeout", 30),
                timestamp=payload.get("timestamp", 0),
            )

        elif event.signal == COMPUTE_CAPACITY_QUEUE_WAIT:
            # Strict field access — missing fields indicate broken wire protocol
            return create_compute_queue_wait_message(
                request_id=payload["request_id"],
                model_id=payload["model_id"],
                compute_type=payload["compute_type"],
                queue_position=payload["queue_position"],
                active_count=payload["active_count"],
                limit=payload["limit"],
                timestamp_ms=payload["timestamp_ms"],
            )

        elif event.signal == COMPUTE_CAPACITY_QUEUE_ACQUIRED:
            # Strict field access — missing fields indicate broken wire protocol
            return create_compute_queue_acquired_message(
                request_id=payload["request_id"],
                model_id=payload["model_id"],
                compute_type=payload["compute_type"],
                wait_duration_ms=payload["wait_duration_ms"],
                queue_position_at_enqueue=payload["queue_position_at_enqueue"],
                timestamp_ms=payload["timestamp_ms"],
            )

        return None
