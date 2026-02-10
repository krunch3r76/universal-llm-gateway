"""
Monitoring orchestration module - core monitoring coordination.

This module is responsible for:
- Coordinating event publishing
- Managing EventBus integration
- Handling serialization concerns
- Routing events to appropriate loggers
"""

from typing import Any

from universal_logging import get_logger

from .event_logger import EventLogger
from .transport_server import TransportServerManager

logger = get_logger(__name__)


class StargateMonitor:
    """
    Event-based monitoring system for stargate processing.

    Publishes real-time monitoring data about stargate transformations
    to EventBus, which forwards events via TransportServer to GUI.

    All events use consistent EventBus → TransportServer → Transport architecture
    for reliable delivery and better separation of concerns.
    """

    def __init__(
        self,
        enabled: bool = True,
        event_bus=None,
        transport_config: dict | None = None,
    ):
        """
        Initialize StargateMonitor with EventBus architecture.

        Args:
            enabled: Enable monitoring
            event_bus: EventBus instance (REQUIRED for event delivery)
            transport_config: Configuration dict (for future use)
        """
        self.enabled = enabled
        self.event_bus = event_bus
        self.transport_config = transport_config or {}

        if not self.enabled:
            self.event_logger = None
            self.transport_server = None
            return

        # Validate EventBus is provided
        if not self.event_bus:
            logger.warning(
                "⚠️ StargateMonitor: EventBus not provided - monitoring will be disabled!"
            )
            logger.warning(
                "⚠️ Events will not be delivered to GUI. Please pass event_bus parameter."
            )
            self.enabled = False
            self.event_logger = None
            self.transport_server = None
            return

        logger.info("✅ StargateMonitor initialized with EventBus architecture")
        logger.info(
            "📡 All events will be published to EventBus → TransportServer → GUI"
        )

        # Initialize component modules
        self.event_logger = EventLogger(self.event_bus, self._ensure_serializable)
        self.transport_server = TransportServerManager(
            self.enabled,
            self.transport_config,
            event_bus=self.event_bus,  # Pass EventBus to subscribe to events
        )

        # Start async server in background if universal_transport is available
        if self.enabled:
            self.transport_server.start_server_in_background()
            # Subscribe to EventBus events if EventBus is available
            if self.event_bus:
                self.transport_server.subscribe_to_eventbus()

    def _ensure_serializable(self, obj):
        """
        Ensure an object is JSON-serializable.

        Handles common serialization patterns:
        - Pydantic models (has .dict() or .model_dump())
        - Objects with .to_dict() method
        - Objects with .__dict__ attribute
        - Already serializable objects (dict, list, str, int, etc.)

        CRITICAL: Uses mode="python" to preserve nested dicts/schemas.
        mode="json" would corrupt JSON schemas (e.g., "integer" -> "int").
        """
        if obj is None:
            return None

        # Already serializable
        if isinstance(obj, (dict, list, str, int, float, bool)):
            return obj

        # Pydantic models - use mode="python" to preserve data structure
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="python", exclude_unset=True)
        if hasattr(obj, "dict"):
            return obj.dict(exclude_unset=True)

        # Custom serialization methods
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        if hasattr(obj, "to_json"):
            import json

            return json.loads(obj.to_json())

        # Fallback to __dict__ if available
        if hasattr(obj, "__dict__"):
            return obj.__dict__

        # Last resort: convert to string
        logger.warning(f"Object {type(obj)} is not serializable, converting to string")
        return str(obj)

    # Delegate all logging methods to EventLogger
    async def log_chat_completion(self, *args, **kwargs):
        """Log chat completion event"""
        if not self.enabled or not self.event_logger:
            return
        await self.event_logger.log_chat_completion(*args, **kwargs)

    async def log_streaming_chunk(self, *args, **kwargs):
        """Log streaming chunk event"""
        if not self.enabled or not self.event_logger:
            return
        await self.event_logger.log_streaming_chunk(*args, **kwargs)

    async def log_streaming_chunk_async(self, *args, **kwargs):
        """Log streaming chunk event asynchronously"""
        if not self.enabled or not self.event_logger:
            return
        await self.event_logger.log_streaming_chunk_async(*args, **kwargs)

    async def log_streaming_chunk_batch(self, *args, **kwargs):
        """Log batched streaming chunks"""
        if not self.enabled or not self.event_logger:
            return
        await self.event_logger.log_streaming_chunk_batch(*args, **kwargs)

    async def log_parameter_comparison(self, *args, **kwargs):
        """Log parameter comparison event"""
        if not self.enabled or not self.event_logger:
            return
        await self.event_logger.log_parameter_comparison(*args, **kwargs)

    async def log_stargate_error(self, *args, **kwargs):
        """Log stargate error event"""
        if not self.enabled or not self.event_logger:
            return
        await self.event_logger.log_stargate_error(*args, **kwargs)

    async def log_request_info(self, *args, **kwargs):
        """Log request info event"""
        if not self.enabled or not self.event_logger:
            return
        await self.event_logger.log_request_info(*args, **kwargs)

    async def log_pre_processing(self, *args, **kwargs):
        """Log pre-processing event"""
        if not self.enabled or not self.event_logger:
            return
        await self.event_logger.log_pre_processing(*args, **kwargs)

    # Delegate transport server methods
    async def async_send_event(
        self, event_type: str, event_data: dict[str, Any]
    ) -> bool:
        """Send event directly through AsyncMonitoringServer if available"""
        if not self.enabled or not self.transport_server:
            return False
        return await self.transport_server.async_send_event(event_type, event_data)

    def send_event_nonblocking(
        self, event_type: str, event_data: dict[str, Any]
    ) -> bool:
        """Non-blocking wrapper for async_send_event"""
        if not self.enabled or not self.transport_server:
            return False
        return self.transport_server.send_event_nonblocking(event_type, event_data)

    async def close_async(self):
        """Close async server gracefully"""
        if self.transport_server:
            await self.transport_server.close_async()

    def close(self):
        """Close monitoring system"""
        logger.info("StargateMonitor closed")
        # EventBus/TransportServer lifecycle managed separately
        if self.transport_server:
            self.transport_server.close()
