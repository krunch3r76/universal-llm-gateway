"""
TCP connection state management and lifecycle.

This module handles connection establishment, state tracking, and cleanup
for TCP transport connections.
"""

import asyncio
import socket
from enum import Enum

from universal_logging import get_logger

logger = get_logger(__name__)


class AsyncTCPConnectionState(Enum):
    """Connection states for async TCP transport."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CLOSING = "closing"
    ERROR = "error"


class AsyncTCPTransportError(Exception):
    """Base exception for async TCP transport errors."""

    pass


class AsyncTCPConnectionError(AsyncTCPTransportError):
    """Raised when connection establishment fails."""

    pass


class AsyncTCPSendError(AsyncTCPTransportError):
    """Raised when sending data fails."""

    pass


class AsyncTCPReceiveError(AsyncTCPTransportError):
    """Raised when receiving data fails."""

    pass


class TCPConnectionManager:
    """
    Manages TCP connection lifecycle and state.

    Handles connection establishment, state tracking, and cleanup
    for TCP transport connections.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 0,
        connection_timeout: float = 10.0,
        family: socket.AddressFamily = socket.AF_UNSPEC,
    ):
        """
        Initialize TCP connection manager.

        Args:
            host: Server hostname or IP address
            port: Server port number
            connection_timeout: Timeout for connection establishment
            family: Address family (AF_INET, AF_INET6, or AF_UNSPEC for auto)
        """
        self.host = host
        self.port = port
        self.connection_timeout = connection_timeout
        self.family = family

        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.state = AsyncTCPConnectionState.DISCONNECTED

        logger.debug(f"TCP connection manager initialized: {host}:{port}")

    async def connect(self) -> None:
        """
        Connect to TCP server.

        Raises:
            AsyncTCPConnectionError: If connection fails
        """
        if self.state == AsyncTCPConnectionState.CONNECTED:
            logger.debug("Already connected")
            return

        self.state = AsyncTCPConnectionState.CONNECTING

        try:
            # Establish async connection
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host=self.host, port=self.port, family=self.family
                ),
                timeout=self.connection_timeout,
            )

            self.state = AsyncTCPConnectionState.CONNECTED

            # Get actual connection details
            peername = self.writer.get_extra_info("peername")
            sockname = self.writer.get_extra_info("sockname")

            logger.info(f"Connected to TCP server: {peername} (from {sockname})")

        except TimeoutError:
            self.state = AsyncTCPConnectionState.ERROR
            raise AsyncTCPConnectionError(
                f"Connection timeout after {self.connection_timeout}s: {self.host}:{self.port}"
            )
        except ConnectionRefusedError:
            self.state = AsyncTCPConnectionState.ERROR
            raise AsyncTCPConnectionError(
                f"Connection refused: {self.host}:{self.port}. "
                f"Ensure the server is running and accepting connections."
            )
        except socket.gaierror as e:
            self.state = AsyncTCPConnectionState.ERROR
            raise AsyncTCPConnectionError(
                f"Failed to resolve hostname {self.host}: {e}"
            )
        except OSError as e:
            self.state = AsyncTCPConnectionState.ERROR
            if e.errno == 113:  # No route to host
                raise AsyncTCPConnectionError(
                    f"No route to host: {self.host}:{self.port}"
                )
            elif e.errno == 101:  # Network unreachable
                raise AsyncTCPConnectionError(
                    f"Network unreachable: {self.host}:{self.port}"
                )
            else:
                raise AsyncTCPConnectionError(f"Network error: {e}")
        except Exception as e:
            self.state = AsyncTCPConnectionState.ERROR
            raise AsyncTCPConnectionError(
                f"Failed to connect to {self.host}:{self.port}: {e}"
            )

    async def close(self) -> None:
        """Close the TCP connection cleanly."""
        if self.state == AsyncTCPConnectionState.CLOSING:
            return

        self.state = AsyncTCPConnectionState.CLOSING

        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception as e:
                logger.warning(f"Error closing writer: {e}")
            self.writer = None

        self.reader = None
        self.state = AsyncTCPConnectionState.DISCONNECTED

        logger.info(f"TCP connection closed: {self.host}:{self.port}")

    async def handle_disconnect(self) -> None:
        """Handle unexpected disconnection."""
        logger.warning("Handling unexpected disconnection")
        self.state = AsyncTCPConnectionState.DISCONNECTED

        # Clean up resources
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
            self.writer = None

        self.reader = None

    def is_connected(self) -> bool:
        """Check if transport is connected."""
        return (
            self.state == AsyncTCPConnectionState.CONNECTED
            and self.reader is not None
            and self.writer is not None
        )

    def get_peer_address(self) -> tuple[str, int] | None:
        """Get remote peer address."""
        if self.writer:
            return self.writer.get_extra_info("peername")
        return None

    def get_local_address(self) -> tuple[str, int] | None:
        """Get local socket address."""
        if self.writer:
            return self.writer.get_extra_info("sockname")
        return None
