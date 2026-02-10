"""
Transport server that broadcasts events to multiple transports.

Subscribes to EventBus and forwards monitoring events to all configured transports.
Pure async implementation - no threads, no locks.
"""

from typing import Any

from universal_event_bus import Event, EventBus
from universal_logging import get_logger

from .base import EventTransport
from .tcp_stream import TCPStreamTransport
from .udp_datagram import UDPDatagramTransport
from .unix_stream import UnixStreamTransport

logger = get_logger(__name__)

# Define monitoring event signals
MONITORING_CHAT_COMPLETION = "monitoring.chat_completion"
MONITORING_STREAMING_CHUNK = "monitoring.streaming_chunk"
MONITORING_REQUEST_INFO = "monitoring.request_info"
MONITORING_PRE_PROCESSING = "monitoring.pre_processing"
MONITORING_PARAMETER_COMPARISON = "monitoring.parameter_comparison"
MONITORING_ERROR = "monitoring.error"


class TransportServer:
    """
    Transport server that manages multiple event transports.

    Subscribes to monitoring events from EventBus and broadcasts them
    to all configured transports (Unix socket, UDP, TCP).

    Thread Safety: Not needed. All operations in single async context.
    """

    def __init__(self, event_bus: EventBus, config: dict[str, Any] | None = None):
        """
        Initialize transport server.

        Args:
            event_bus: EventBus instance to subscribe to
            config: Configuration dict with transport settings
        """
        self.event_bus = event_bus
        self.config = config or {}
        self.transports: list[EventTransport] = []
        self._started = False

        # Initialize transports based on config
        self._initialize_transports()

        # Subscribe to monitoring events
        self._subscribe_to_events()

    def _initialize_transports(self):
        """Initialize all configured transports"""
        transport_config = self.config.get("transports", {})

        # Unix stream transport (default, recommended for local GUI)
        if transport_config.get("unix_socket", {}).get("enabled", True):
            unix_config = transport_config.get("unix_socket", {})
            try:
                transport = UnixStreamTransport(unix_config)
                self.transports.append(transport)
                logger.info("Initialized Unix stream transport")
            except Exception as e:
                logger.error(f"Failed to initialize Unix stream transport: {e}")

        # UDP transport (for streaming chunks and backward compatibility)
        if transport_config.get("udp", {}).get("enabled", True):
            udp_config = transport_config.get("udp", {})
            try:
                transport = UDPDatagramTransport(udp_config)
                self.transports.append(transport)
                logger.info("Initialized UDP transport")
            except Exception as e:
                logger.error(f"Failed to initialize UDP transport: {e}")

        # TCP transport (optional, for remote GUIs)
        if transport_config.get("tcp", {}).get("enabled", False):
            tcp_config = transport_config.get("tcp", {})
            try:
                transport = TCPStreamTransport(tcp_config)
                self.transports.append(transport)
                logger.info("Initialized TCP transport")
            except Exception as e:
                logger.error(f"Failed to initialize TCP transport: {e}")

        logger.info(f"Initialized {len(self.transports)} transports")

    def _subscribe_to_events(self):
        """Subscribe to monitoring events on EventBus"""
        # Subscribe to all monitoring event types
        self.event_bus.subscribe_async(MONITORING_CHAT_COMPLETION, self._handle_event)
        self.event_bus.subscribe_async(MONITORING_STREAMING_CHUNK, self._handle_event)
        self.event_bus.subscribe_async(MONITORING_REQUEST_INFO, self._handle_event)
        self.event_bus.subscribe_async(MONITORING_PRE_PROCESSING, self._handle_event)
        self.event_bus.subscribe_async(
            MONITORING_PARAMETER_COMPARISON, self._handle_event
        )
        self.event_bus.subscribe_async(MONITORING_ERROR, self._handle_event)

        logger.info("Subscribed to all monitoring events on EventBus")

    async def _handle_event(self, event: Event):
        """
        Handle incoming event from EventBus.

        Args:
            event: Event instance from EventBus
        """
        try:
            # Convert Event to dict for transport
            event_data = {
                "id": event.id,
                "timestamp": event.timestamp,
                "signal": event.signal,
                **event.payload,  # Unpack payload into event_data
            }

            # Broadcast to all transports
            await self.broadcast_event(event_data)

        except Exception as e:
            logger.error(f"Error handling event: {e}")

    async def start(self):
        """Start all transports (async)."""
        if self._started:
            logger.warning("TransportServer already started")
            return

        success_count = 0
        for transport in self.transports:
            try:
                await transport.start()
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to start {transport.transport_name}: {e}")

        self._started = True
        total = len(self.transports)
        logger.info(f"✅ TransportServer started ({success_count}/{total} active)")

    async def stop(self):
        """Stop all transports (async)."""
        if not self._started:
            return

        for transport in self.transports:
            try:
                await transport.stop()
            except Exception as e:
                logger.error(f"Error stopping {transport.transport_name}: {e}")

        self._started = False
        logger.info("TransportServer stopped")

    async def broadcast_event(self, event_data: dict[str, Any]):
        """
        Broadcast event to all enabled transports (async).

        Args:
            event_data: Event dictionary to broadcast
        """
        if not self._started:
            return

        # Broadcast to ALL enabled transports
        for transport in self.transports:
            if transport.enabled:
                try:
                    await transport.send_event(event_data)
                except Exception as e:
                    logger.debug(
                        f"Failed to send event via {transport.transport_name}: {e}"
                    )

    async def send_direct(self, event_data: dict[str, Any]):
        """
        Send event directly without going through EventBus (async).

        Useful for backward compatibility or direct sending.

        Args:
            event_data: Event dictionary to send
        """
        await self.broadcast_event(event_data)
