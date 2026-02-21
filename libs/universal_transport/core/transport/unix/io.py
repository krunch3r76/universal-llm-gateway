"""
Unix socket I/O operations.

This module handles sending and receiving data over Unix socket connections.

Mirrors tcp/io.py — send/receive/receive_with_timeout/close/is_connected are
intentionally kept in sync. If you change the shared logic here, apply the
same change there. A shared base is deferred until a third transport type exists.
"""

import asyncio
from universal_logging import get_logger
from pathlib import Path

from .connection import (
    AsyncUnixReceiveError,
    AsyncUnixSendError,
    AsyncUnixTransportError,
    UnixConnectionManager,
)

logger = get_logger(__name__)


class AsyncUnixTransport:
    """
    Async Unix domain socket transport.

    Provides async Unix socket communication with proper buffer management
    that avoids asyncio readline buffer limits. Uses readexactly() for
    deterministic data reading based on length-prefixed framing.

    Features:
    - Async I/O using asyncio primitives
    - No readline() usage (avoids buffer limits)
    - Works optimally with length-prefixed protocols
    - Automatic connection management
    - Proper resource cleanup

    Attributes:
        connection: Connection manager for lifecycle handling
    """

    def __init__(self, socket_path: str, connection_timeout: float = 10.0):
        """
        Initialize async Unix transport.

        Args:
            socket_path: Path to Unix socket file
            connection_timeout: Timeout for connection establishment
        """
        self.connection = UnixConnectionManager(socket_path, connection_timeout)

    async def connect(self) -> None:
        """Connect to Unix socket server."""
        await self.connection.connect()

    async def send(self, data: bytes) -> None:
        """
        Send raw bytes over Unix socket.

        Args:
            data: Bytes to send

        Raises:
            AsyncUnixSendError: If sending fails
            AsyncUnixTransportError: If not connected
        """
        if not self.connection.is_connected():
            raise AsyncUnixTransportError("Not connected")

        if not self.connection.writer:
            raise AsyncUnixSendError("Writer not available")

        try:
            self.connection.writer.write(data)
            await self.connection.writer.drain()

            logger.debug(f"Sent {len(data)} bytes")

        except ConnectionResetError as e:
            logger.error(f"Connection reset during send: {e}")
            await self.connection.handle_disconnect()
            raise AsyncUnixSendError(f"Connection lost: {e}")
        except BrokenPipeError as e:
            logger.error(f"Broken pipe during send: {e}")
            await self.connection.handle_disconnect()
            raise AsyncUnixSendError(f"Connection lost: {e}")
        except Exception as e:
            raise AsyncUnixSendError(f"Failed to send data: {e}")

    async def receive(self, nbytes: int) -> bytes:
        """
        Receive exactly nbytes from Unix socket.

        This method uses asyncio.readexactly() which eliminates buffer
        limit issues that plague readline()-based approaches.

        Args:
            nbytes: Exact number of bytes to read

        Returns:
            Exactly nbytes of data

        Raises:
            AsyncUnixReceiveError: If receiving fails
            AsyncUnixTransportError: If not connected
        """
        if not self.connection.is_connected():
            raise AsyncUnixTransportError("Not connected")

        if not self.connection.reader:
            raise AsyncUnixReceiveError("Reader not available")

        try:
            # Use readexactly() - this is the key to avoiding buffer limits
            data = await self.connection.reader.readexactly(nbytes)

            logger.debug(f"Received {len(data)} bytes (requested {nbytes})")
            return data

        except asyncio.IncompleteReadError as e:
            # Connection closed by peer
            logger.info(f"Connection closed by peer (incomplete read): {e}")
            await self.connection.handle_disconnect()
            raise AsyncUnixReceiveError(f"Connection closed: {e}")
        except ConnectionResetError as e:
            logger.error(f"Connection reset during receive: {e}")
            await self.connection.handle_disconnect()
            raise AsyncUnixReceiveError(f"Connection lost: {e}")
        except Exception as e:
            raise AsyncUnixReceiveError(f"Failed to receive data: {e}")

    async def receive_with_timeout(self, nbytes: int, timeout: float) -> bytes:
        """
        Receive exactly nbytes with timeout.

        Args:
            nbytes: Exact number of bytes to read
            timeout: Timeout in seconds

        Returns:
            Exactly nbytes of data

        Raises:
            asyncio.TimeoutError: If timeout occurs
            AsyncUnixReceiveError: If receiving fails
        """
        return await asyncio.wait_for(self.receive(nbytes), timeout=timeout)

    async def close(self) -> None:
        """Close the Unix socket connection cleanly."""
        await self.connection.close()

    def is_connected(self) -> bool:
        """Check if transport is connected."""
        return self.connection.is_connected()

    @property
    def socket_path(self) -> Path:
        """Get socket path."""
        return self.connection.socket_path

    @property
    def state(self):
        """Get connection state."""
        return self.connection.state

    def __str__(self) -> str:
        return f"AsyncUnixTransport({self.connection.socket_path}, state={self.connection.state.value})"

    def __repr__(self) -> str:
        return (
            f"AsyncUnixTransport(socket_path='{self.connection.socket_path}', "
            f"state={self.connection.state.value})"
        )
