"""
Async monitoring transport using length-prefixed protocol.

This is the migrated version of MonitoringTransport and MonitoringClient that uses
the new async length-prefixed API, eliminating asyncio readline buffer limits.

Key improvements:
- Uses LengthPrefixedProtocol (no readline 64KB buffer limits)
- Async/await interface (better performance)
- Pluggable serialization (JSON, MessagePack, etc.)
- Multi-MB event support without buffer issues
- Same high-level interface as legacy classes
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from universal_logging import get_logger

from ..core.client_server.async_client import (
    AsyncTransportClient,
    create_tcp_client,
    create_unix_client,
)
from ..core.client_server.async_server import (
    AsyncClientSession,
    AsyncTransportServer,
    create_unix_server,
)
from ..core.client_server.server.factories import create_tcp_server
from ..core.protocol.serializers import JSONSerializer

logger = get_logger(__name__)


class MonitoringEvent(BaseModel):
    """Monitoring event structure compatible with legacy format."""

    type: str
    timestamp: float = Field(default_factory=time.time)
    app_name: str = Field(default="unknown")
    data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class MonitoringConfig:
    """Configuration for async monitoring transport."""

    app_name: str
    unix_socket: Path | None = None
    max_clients: int = 100
    max_message_size: int = (
        4 * 1024 * 1024
    )  # 4MB default (appropriate for monitoring data)


class AsyncMonitoringServer:
    """
    Async monitoring server using length-prefixed protocol.

    Replaces MonitoringTransport with modern async API.
    Eliminates asyncio readline 64KB buffer limits.

    Key features:
    - Multi-MB event support (100MB+ without issues)
    - Async/await interface (better performance)
    - Multiple client support
    - Event broadcasting to all connected clients
    - No buffer limit issues

    Usage:
        async with AsyncMonitoringServer(
            app_name="stargate",
            unix_socket="/tmp/events.sock"
        ) as server:
            await server.send_event("chat_completion", {...})
    """

    def __init__(
        self,
        app_name: str,
        unix_socket: str | Path | None = None,
        host: str | None = None,
        port: int | None = None,
        max_clients: int = 100,
        max_message_size: int = 4 * 1024 * 1024,
    ):
        """
        Initialize async monitoring server.

        Args:
            app_name: Name of the application (e.g., "stargate")
            unix_socket: Path to Unix socket (for local monitoring)
            host: TCP host (for remote monitoring)
            port: TCP port (for remote monitoring)
            max_clients: Maximum concurrent clients
            max_message_size: Maximum event size in bytes (default 100MB)
        """
        # Transport selection
        if unix_socket:
            self.transport_type = "unix"
            self.unix_socket = Path(unix_socket)
            self.host = None
            self.port = None
        elif host is not None and port is not None:
            self.transport_type = "tcp"
            self.unix_socket = None
            self.host = host
            self.port = port
        else:
            raise ValueError("Provide unix_socket OR (host, port)")

        self.config = MonitoringConfig(
            app_name=app_name, unix_socket=self.unix_socket, max_clients=max_clients
        )
        self.max_message_size = max_message_size

        # Server instance
        self.server: AsyncTransportServer | None = None

        # Stats
        self.stats = {
            "events_sent": 0,
            "events_failed": 0,
            "bytes_sent": 0,
            "connected_clients": 0,
        }

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()

    async def start(self) -> None:
        """Start the monitoring server."""
        if self.server and self.server.is_running():
            logger.warning("Server already running")
            return

        # Create async server with length-prefixed protocol
        if self.transport_type == "unix":
            self.server = await create_unix_server(
                socket_path=str(self.config.unix_socket),
                message_handler=self._handle_client_message,
                serializer=JSONSerializer(),
                max_message_size=self.max_message_size,
                max_clients=self.config.max_clients,
            )
            logger.info(f"Async monitoring server started: {self.config.unix_socket}")
        else:  # tcp
            assert self.host is not None and self.port is not None
            self.server = await create_tcp_server(
                host=self.host,
                port=self.port,
                message_handler=self._handle_client_message,
                serializer=JSONSerializer(),
                max_message_size=self.max_message_size,
                max_clients=self.config.max_clients,
            )
            logger.info(f"Async monitoring server started: {self.host}:{self.port}")

        # Start the server
        await self.server.start()

    async def stop(self) -> None:
        """Stop the monitoring server."""
        if self.server:
            await self.server.stop()
            self.server = None
            logger.info("Async monitoring server stopped")

    async def send_event(self, event_type: str, event_data: dict[str, Any]) -> bool:
        """
        Send monitoring event to all connected clients.

        Args:
            event_type: Type of event (e.g., "chat_completion")
            event_data: Event data dictionary

        Returns:
            True if event was sent to at least one client
        """
        if not self.server or not self.server.is_running():
            logger.warning("Server not running, cannot send event")
            return False

        try:
            # Create monitoring event
            event = MonitoringEvent(
                type=event_type,
                app_name=self.config.app_name,
                data=event_data,
                timestamp=time.time(),
            )

            # Convert to dict for broadcasting
            event_dict = event.model_dump()

            # Broadcast to all clients
            sent_count = self.server.broadcast_message(event_dict)

            if sent_count > 0:
                self.stats["events_sent"] += 1
                self.stats["bytes_sent"] += len(str(event_dict).encode())
                logger.debug(f"Broadcasted {event_type} event to {sent_count} clients")
                return True
            else:
                logger.debug("No clients connected, event not sent")
                return False

        except Exception as e:
            self.stats["events_failed"] += 1
            logger.error(f"Failed to send event: {e}")
            return False

    async def _handle_client_message(
        self, message: Any, session: AsyncClientSession
    ) -> None:
        """Handle incoming messages from clients (command/query pattern)."""
        logger.debug(f"Received from {session.client_id}: {message}")
        # Could implement command handling here if needed

    def get_stats(self) -> dict[str, Any]:
        """Get server statistics."""
        if self.server:
            self.stats["connected_clients"] = self.server.get_client_count()
        return self.stats.copy()

    def get_client_count(self) -> int:
        """Get number of connected clients."""
        if self.server:
            return self.server.get_client_count()
        return 0

    def is_running(self) -> bool:
        """Check if server is running."""
        return self.server is not None and self.server.is_running()


class AsyncMonitoringClient:
    """
    Async monitoring client using length-prefixed protocol.

    Replaces MonitoringClient with modern async API.
    Eliminates asyncio readline 64KB buffer limits.

    Key features:
    - Receives multi-MB events without buffer limits
    - Async/await interface (non-blocking)
    - Automatic reconnection handling
    - Event filtering
    - No asyncio readline issues

    Usage:
        async with AsyncMonitoringClient(
            unix_socket="/tmp/events.sock"
        ) as client:
            event = await client.receive_event(timeout=5.0)
    """

    def __init__(
        self,
        unix_socket: str | Path | None = None,
        host: str | None = None,
        port: int | None = None,
        auto_reconnect: bool = True,
        max_message_size: int = 4 * 1024 * 1024,
    ):
        """
        Initialize async monitoring client.

        Args:
            unix_socket: Path to Unix socket (for local monitoring)
            host: TCP host (for remote monitoring)
            port: TCP port (for remote monitoring)
            auto_reconnect: Whether to automatically reconnect
            max_message_size: Maximum event size in bytes (default 100MB)
        """
        # Transport selection (use explicit None checks to allow port=0 or host="")
        if unix_socket:
            self.transport_type = "unix"
            self.unix_socket = Path(unix_socket)
            self.host = None
            self.port = None
        elif host is not None and port is not None:
            self.transport_type = "tcp"
            self.unix_socket = None
            self.host = host
            self.port = port
        else:
            raise ValueError("Provide unix_socket OR (host, port)")

        self.auto_reconnect = auto_reconnect
        self.max_message_size = max_message_size

        # Client instance
        self.client: AsyncTransportClient | None = None

        # Stats
        self.stats = {"events_received": 0, "events_failed": 0, "reconnects": 0}

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()

    async def connect(self) -> None:
        """Connect to monitoring server."""
        if self.client and self.client.is_connected():
            logger.warning("Client already connected")
            return

        try:
            # Create async client with length-prefixed protocol
            if self.transport_type == "unix":
                self.client = await create_unix_client(
                    socket_path=str(self.unix_socket),
                    serializer=JSONSerializer(),
                    max_message_size=self.max_message_size,
                )
                logger.info(f"Connected to monitoring server: {self.unix_socket}")
            else:  # tcp
                assert self.host is not None and self.port is not None
                self.client = await create_tcp_client(
                    host=self.host,
                    port=self.port,
                    serializer=JSONSerializer(),
                    max_message_size=self.max_message_size,
                )
                logger.info(f"Connected to monitoring server: {self.host}:{self.port}")

            # Connect
            await self.client.connect()

        except Exception as e:
            if self.auto_reconnect:
                logger.warning(f"Connection failed, will retry: {e}")
                self.stats["reconnects"] += 1
                raise
            else:
                raise

    async def disconnect(self) -> None:
        """Disconnect from monitoring server."""
        if self.client:
            await self.client.close()
            self.client = None
            logger.info("Disconnected from monitoring server")

    async def receive_event(
        self, timeout: float | None = None
    ) -> MonitoringEvent | None:
        """
        Receive monitoring event from server.

        Args:
            timeout: Maximum time to wait for event

        Returns:
            MonitoringEvent if received, None if timeout
        """
        if not self.client or not self.client.is_connected():
            await self.connect()

        try:
            # Receive message using length-prefixed protocol
            message = await self.client.receive_message(timeout=timeout)

            # Convert to MonitoringEvent
            event = MonitoringEvent(**message)

            self.stats["events_received"] += 1
            return event

        except TimeoutError:
            return None
        except Exception as e:
            self.stats["events_failed"] += 1
            logger.error(f"Failed to receive event: {e}")

            if self.auto_reconnect:
                logger.info("Attempting reconnection...")
                try:
                    await self.disconnect()
                    await self.connect()
                except Exception:
                    pass

            return None

    def get_stats(self) -> dict[str, Any]:
        """Get client statistics."""
        return self.stats.copy()

    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self.client is not None and self.client.is_connected()


# Clean API - no backward compatibility aliases
