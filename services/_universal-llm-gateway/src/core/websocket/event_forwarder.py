"""Forward EventBus events to WebSocket clients."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

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
    PHANTOM_MODEL_CLEANED,
    PHANTOM_MODEL_DETECTED,
    REQUEST_INFERENCE_STARTED,
    SYSTEM_RESOURCES_UPDATED,
    VRAM_PHANTOM_DETECTED,
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
    create_phantom_model_cleaned_message,
    create_phantom_model_detected_message,
    create_request_inference_started_message,
    create_resource_update_message,
    create_vram_phantom_detected_message,
)

if TYPE_CHECKING:
    from .init_cache import InitDataCache

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

    # Events to forward to Stargate
    FORWARDED_EVENTS = [
        MODEL_LOADING_STARTED,
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
        # Compute capacity telemetry (orchestration observability)
        COMPUTE_CAPACITY_QUEUE_WAIT,
        COMPUTE_CAPACITY_QUEUE_ACQUIRED,
        VRAM_PHANTOM_DETECTED,
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
        # Skip if stopped (handlers can't be unsubscribed in EventBus v0.2.0+)
        if not self._subscribed:
            return

        # Create task for async message conversion and broadcast
        _ = asyncio.create_task(self._process_and_broadcast(event))

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
        except (KeyError, TypeError, ValueError) as e:
            logger.error(
                "Failed to process malformed event %s: %s",
                event.signal,
                e,
                exc_info=True,
            )
        except Exception:
            logger.exception("Unexpected failure processing event %s", event.signal)
            raise

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

        sync_builders: dict[
            str, Callable[[dict[str, Any]], WebSocketMessage | None]
        ] = {
            MODEL_LOADING_STARTED: lambda p: create_model_loading_started_message(
                model_id=p.get("model_id", "unknown")
            ),
            MODEL_LOADED: lambda p: create_model_loaded_message(
                model_id=p.get("model_id", "unknown"),
                vram_mb=p.get("vram_usage_mb", 0),
                ram_mb=p.get("ram_usage_mb", 0),
                context_length=p.get("context_length"),
            ),
            MODEL_LOAD_FAILED: lambda p: create_model_load_failed_message(
                model_id=p.get("model_id", "unknown"),
                error_message=p.get("error_message", "Unknown error"),
            ),
            MODEL_UNLOADED: lambda p: create_model_unloaded_message(
                model_id=p.get("model_id", "unknown")
            ),
            INFERENCE_STARTED: lambda p: create_model_busy_message(
                model_id=p.get("model_id", "unknown")
            ),
            INFERENCE_COMPLETED: lambda p: create_model_idle_message(
                model_id=p.get("model_id", "unknown"),
                last_inference_time=p.get("last_inference_time", 0.0),
            ),
            REQUEST_INFERENCE_STARTED: self._build_request_inference_started_message,
            SYSTEM_RESOURCES_UPDATED: self._build_system_resources_update_message,
            GATEWAY_SHUTDOWN: lambda p: create_gateway_shutdown_message(
                gateway_id=p.get("gateway_id", "unknown"),
                reason=p.get("reason", "unknown"),
                timestamp=p.get("timestamp", 0),
            ),
            GATEWAY_DRAINING: lambda p: create_gateway_draining_message(
                gateway_id=p.get("gateway_id", "unknown"),
                reason=p.get("reason", "unknown"),
                timeout=p.get("timeout", 30),
                timestamp=p.get("timestamp", 0),
            ),
            COMPUTE_CAPACITY_QUEUE_WAIT: lambda p: create_compute_queue_wait_message(
                request_id=p["request_id"],
                model_id=p["model_id"],
                compute_type=p["compute_type"],
                queue_position=p["queue_position"],
                active_count=p["active_count"],
                limit=p["limit"],
                timestamp_ms=p["timestamp_ms"],
            ),
            COMPUTE_CAPACITY_QUEUE_ACQUIRED: lambda p: create_compute_queue_acquired_message(
                request_id=p["request_id"],
                model_id=p["model_id"],
                compute_type=p["compute_type"],
                wait_duration_ms=p["wait_duration_ms"],
                queue_position_at_enqueue=p["queue_position_at_enqueue"],
                timestamp_ms=p["timestamp_ms"],
            ),
            VRAM_PHANTOM_DETECTED: self._build_vram_phantom_detected_message,
            PHANTOM_MODEL_DETECTED: self._build_phantom_model_detected_message,
            PHANTOM_MODEL_CLEANED: self._build_phantom_model_cleaned_message,
        }
        async_builders: dict[
            str, Callable[[dict[str, Any]], Awaitable[WebSocketMessage | None]]
        ] = {
            CATALOG_RELOADED: self._build_catalog_update_message,
        }

        if builder := sync_builders.get(event.signal):
            return builder(payload)
        if async_builder := async_builders.get(event.signal):
            return await async_builder(payload)
        return None

    def _build_request_inference_started_message(
        self, payload: dict[str, Any]
    ) -> WebSocketMessage | None:
        """Build request-scoped runtime-start message with strict payload checks."""
        try:
            return create_request_inference_started_message(
                request_id=payload["request_id"],
                model_id=payload["model_id"],
                gateway_url=payload["gateway_url"],
                correlation_id=payload.get("correlation_id"),
            )
        except KeyError:
            logger.exception(
                "Malformed request.inference.started payload in event_forwarder: "
                "keys=%s payload=%s",
                list(payload.keys()),
                payload,
            )
            return None

    def _build_system_resources_update_message(
        self, payload: dict[str, Any]
    ) -> WebSocketMessage:
        """Build resource update message with forwarding diagnostics."""
        logger.info(
            f"📡 Forwarding SYSTEM_RESOURCES_UPDATED to Stargate: "
            f"available_vram={payload.get('available_vram_mb', 0)}MB, "
            f"available_ram={payload.get('available_ram_mb', 0)}MB"
        )
        return create_resource_update_message(
            available_vram_mb=payload.get("available_vram_mb", 0),
            available_ram_mb=payload.get("available_ram_mb", 0),
            total_vram_mb=payload.get("total_vram_mb"),
            total_ram_mb=payload.get("total_ram_mb"),
            model_vram=payload.get("model_vram"),
        )

    async def _build_catalog_update_message(
        self, payload: dict[str, Any]
    ) -> WebSocketMessage:
        """Build catalog update message with fresh cache data when available."""
        reason = payload.get("reason", "reload")
        models = None
        catalog = None

        if self._init_cache:
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

    def _build_vram_phantom_detected_message(
        self, payload: dict[str, Any]
    ) -> WebSocketMessage:
        return create_vram_phantom_detected_message(
            hardware_used_mb=payload.get("hardware_used_mb", 0),
            catalog_used_mb=payload.get("catalog_used_mb", 0),
            discrepancy_mb=payload.get("discrepancy_mb", 0),
            tracked_models=payload.get("tracked_models", []),
        )

    def _build_phantom_model_detected_message(
        self, payload: dict[str, Any]
    ) -> WebSocketMessage:
        return create_phantom_model_detected_message(
            model_id=payload.get("model_id", "unknown"),
            process_status=payload.get("process_status", "unknown"),
            tracker_status=payload.get("tracker_status"),
        )

    def _build_phantom_model_cleaned_message(
        self, payload: dict[str, Any]
    ) -> WebSocketMessage:
        return create_phantom_model_cleaned_message(
            model_id=payload.get("model_id", "unknown"),
            success=bool(payload.get("success", False)),
            vram_freed_mb=payload.get("vram_freed_mb"),
        )
