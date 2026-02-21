"""
TCP I/O operations.

This module handles sending and receiving data over TCP connections.

Mirrors unix/io.py — send/receive/receive_with_timeout/close/is_connected are
intentionally kept in sync. If you change the shared logic here, apply the
same change there. A shared base is deferred until a third transport type exists.
"""

import asyncio
from universal_logging import get_logger
from .connection import (
    AsyncTCPReceiveError,
    AsyncTCPSendError,
    AsyncTCPTransportError,
    TCPConnectionManager,
)

logger = get_logger(__name__)


class AsyncTCPTransport:
    """
    Async TCP socket transport.

    Provides async TCP socket communication with proper buffer management
    that avoids asyncio readline buffer limits. Uses readexactly() for
    deterministic data reading based on length-prefixed framing.

    Features:
    - Async I/O using asyncio primitives
    - No readline() usage (avoids buffer limits)
    - Works optimally with length-prefixed protocols
    - IPv4 and IPv6 support
    - Configurable timeouts
    - Proper resource cleanup

    Attributes:
        connection: Connection manager for lifecycle handling
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 0,
        connection_timeout: float = 10.0,
        family=None,
    ):
        """
        Initialize async TCP transport.

        Args:
            host: Server hostname or IP address
            port: Server port number
            connection_timeout: Timeout for connection establishment
            family: Address family (AF_INET, AF_INET6, or AF_UNSPEC for auto)
        """
        import socket

        if family is None:
            family = socket.AF_UNSPEC

        self.connection = TCPConnectionManager(
            host=host, port=port, connection_timeout=connection_timeout, family=family
        )

    async def connect(self) -> None:
        """Connect to TCP server."""
        await self.connection.connect()

    async def send(self, data: bytes) -> None:
        """
        Send raw bytes over TCP socket.

        Args:
            data: Bytes to send

        Raises:
            AsyncTCPSendError: If sending fails
            AsyncTCPTransportError: If not connected
        """
        if not self.connection.is_connected():
            raise AsyncTCPTransportError("Not connected")

        if not self.connection.writer:
            raise AsyncTCPSendError("Writer not available")

        try:
            self.connection.writer.write(data)
            await self.connection.writer.drain()

            logger.debug(f"Sent {len(data)} bytes")

        except ConnectionResetError as e:
            logger.error(f"Connection reset during send: {e}")
            await self.connection.handle_disconnect()
            raise AsyncTCPSendError(f"Connection lost: {e}")
        except BrokenPipeError as e:
            logger.error(f"Broken pipe during send: {e}")
            await self.connection.handle_disconnect()
            raise AsyncTCPSendError(f"Connection lost: {e}")
        except Exception as e:
            raise AsyncTCPSendError(f"Failed to send data: {e}")

    async def receive(self, nbytes: int) -> bytes:
        """
        Receive exactly nbytes from TCP socket.

        This method uses asyncio.readexactly() which eliminates buffer
        limit issues that plague readline()-based approaches.

        Args:
            nbytes: Exact number of bytes to read

        Returns:
            Exactly nbytes of data

        Raises:
            AsyncTCPReceiveError: If receiving fails
            AsyncTCPTransportError: If not connected
        """
        if not self.connection.is_connected():
            raise AsyncTCPTransportError("Not connected")

        if not self.connection.reader:
            raise AsyncTCPReceiveError("Reader not available")

        try:
            # Use readexactly() - this is the key to avoiding buffer limits
            data = await self.connection.reader.readexactly(nbytes)

            logger.debug(f"Received {len(data)} bytes (requested {nbytes})")
            return data

        except asyncio.IncompleteReadError as e:
            # Connection closed by peer
            logger.info(f"Connection closed by peer (incomplete read): {e}")
            await self.connection.handle_disconnect()
            raise AsyncTCPReceiveError(f"Connection closed: {e}")
        except ConnectionResetError as e:
            logger.error(f"Connection reset during receive: {e}")
            await self.connection.handle_disconnect()
            raise AsyncTCPReceiveError(f"Connection lost: {e}")
        except Exception as e:
            raise AsyncTCPReceiveError(f"Failed to receive data: {e}")

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
            AsyncTCPReceiveError: If receiving fails
        """
        return await asyncio.wait_for(self.receive(nbytes), timeout=timeout)

    async def close(self) -> None:
        """Close the TCP connection cleanly."""
        await self.connection.close()

    def is_connected(self) -> bool:
        """Check if transport is connected."""
        return self.connection.is_connected()

    def get_peer_address(self):
        """Get remote peer address."""
        return self.connection.get_peer_address()

    def get_local_address(self):
        """Get local socket address."""
        return self.connection.get_local_address()

    @property
    def host(self) -> str:
        """Get host."""
        return self.connection.host

    @property
    def port(self) -> int:
        """Get port."""
        return self.connection.port

    @property
    def state(self):
        """Get connection state."""
        return self.connection.state

    def __str__(self) -> str:
        return f"AsyncTCPTransport({self.connection.host}:{self.connection.port}, state={self.connection.state.value})"

    def __repr__(self) -> str:
        return (
            f"AsyncTCPTransport(host='{self.connection.host}', port={self.connection.port}, "
            f"state={self.connection.state.value})"
        )
