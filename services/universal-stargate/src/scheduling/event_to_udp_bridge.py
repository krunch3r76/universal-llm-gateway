"""
Event-to-Transport Bridge for Universal Stargate GUI compatibility.

This module bridges the new event-driven architecture with the transport system
used by the GUI. It subscribes to EventBus events and forwards them via the
configured transport (Unix socket by default).
"""

from typing import Any

from universal_event_bus import EventBus
from universal_logging import get_logger

from src.core.transport.server import TransportServer

logger = get_logger(__name__)


class EventToUDPBridge:
    """
    Bridge between EventBus and transport for GUI compatibility.

    Despite the legacy name (EventToUDPBridge), this now uses TransportServer
    to send events via Unix socket, which is the preferred transport for local GUIs.

    This component is transitional - in the future, the GUI should subscribe
    directly to EventBus events rather than through this bridge.
    """

    def __init__(self, event_bus: EventBus, config: dict[str, Any] | None = None):
        """
        Initialize the bridge using TransportServer.

        Args:
            event_bus: EventBus instance to listen to
            config: Transport configuration dict (optional)
                Defaults to Unix socket configuration
        """
        self.event_bus = event_bus

        # Configure transport: Unix socket by default (no UDP)
        transport_config = config or {
            "transports": {
                "unix_socket": {
                    "enabled": True,
                    "socket_path": "/tmp/stargate_events.sock",
                }
            }
        }

        # Initialize TransportServer with EventBus
        # TransportServer automatically subscribes to EventBus and forwards events
        self.transport_server = TransportServer(event_bus, transport_config)

        logger.info("🔗 Event-to-Transport Bridge initialized (using Unix socket)")

    async def start(self):
        """Start the transport server (async)."""
        try:
            await self.transport_server.start()
            logger.info("✅ Event-to-Transport Bridge started")
        except Exception as e:
            logger.error(f"Failed to start Event-to-Transport Bridge: {e}")
            raise

    async def stop(self):
        """Stop the transport server (async)."""
        try:
            await self.transport_server.stop()
            logger.info("🔗 Event-to-Transport Bridge stopped")
        except Exception as e:
            logger.error(f"Error stopping Event-to-Transport Bridge: {e}")

    async def close(self):
        """Alias for stop() for backward compatibility."""
        await self.stop()
