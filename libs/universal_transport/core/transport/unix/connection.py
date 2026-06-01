"""
Unix socket connection state management and lifecycle.

This module handles connection establishment, state tracking, and cleanup
for Unix socket transport connections.
"""

import asyncio
from enum import Enum
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)


class AsyncUnixConnectionState(Enum):
    """Connection states for async Unix transport."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CLOSING = "closing"
    ERROR = "error"


class AsyncUnixTransportError(Exception):
    """Base exception for async Unix transport errors."""

    pass


class AsyncUnixConnectionError(AsyncUnixTransportError):
    """Raised when connection establishment fails."""

    pass


class AsyncUnixSendError(AsyncUnixTransportError):
    """Raised when sending data fails."""

    pass


class AsyncUnixReceiveError(AsyncUnixTransportError):
    """Raised when receiving data fails."""

    pass


class UnixConnectionManager:
    """
    Manages Unix socket connection lifecycle and state.

    Handles connection establishment, state tracking, and cleanup
    for Unix socket transport connections.
    """

    def __init__(self, socket_path: str, connection_timeout: float = 10.0):
        """
        Initialize Unix connection manager.

        Args:
            socket_path: Path to Unix socket file
            connection_timeout: Timeout for connection establishment
        """
        self.socket_path = Path(socket_path)
        self.connection_timeout = connection_timeout

        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.state = AsyncUnixConnectionState.DISCONNECTED

        logger.debug(f"Unix connection manager initialized: {self.socket_path}")

    async def connect(self) -> None:
        """
        Connect to Unix socket server.

        Raises:
            AsyncUnixConnectionError: If connection fails
        """
        if self.state == AsyncUnixConnectionState.CONNECTED:
            logger.debug("Already connected")
            return

        self.state = AsyncUnixConnectionState.CONNECTING

        try:
            # Validate socket file exists
            if not self.socket_path.exists():
                raise AsyncUnixConnectionError(
                    f"Unix socket not found: {self.socket_path}. "
                    f"Ensure the server is running."
                )

            # Establish async connection
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.socket_path)),
                timeout=self.connection_timeout,
            )

            self.state = AsyncUnixConnectionState.CONNECTED
            logger.info(f"Connected to Unix socket: {self.socket_path}")

        except TimeoutError:
            self.state = AsyncUnixConnectionState.ERROR
            raise AsyncUnixConnectionError(
                f"Connection timeout after {self.connection_timeout}s: "
                f"{self.socket_path}"
            )
        except FileNotFoundError:
            self.state = AsyncUnixConnectionState.ERROR
            raise AsyncUnixConnectionError(f"Unix socket not found: {self.socket_path}")
        except PermissionError:
            self.state = AsyncUnixConnectionState.ERROR
            raise AsyncUnixConnectionError(f"Permission denied: {self.socket_path}")
        except Exception as e:
            self.state = AsyncUnixConnectionState.ERROR
            raise AsyncUnixConnectionError(
                f"Failed to connect to {self.socket_path}: {e}"
            )

    async def close(self) -> None:
        """Close the Unix socket connection cleanly."""
        if self.state == AsyncUnixConnectionState.CLOSING:
            return

        self.state = AsyncUnixConnectionState.CLOSING

        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception as e:
                logger.warning(f"Error closing writer: {e}")
            self.writer = None

        self.reader = None
        self.state = AsyncUnixConnectionState.DISCONNECTED

        logger.info(f"Unix socket connection closed: {self.socket_path}")

    async def handle_disconnect(self) -> None:
        """Handle unexpected disconnection."""
        logger.warning("Handling unexpected disconnection")
        self.state = AsyncUnixConnectionState.DISCONNECTED

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
            self.state == AsyncUnixConnectionState.CONNECTED
            and self.reader is not None
            and self.writer is not None
        )
