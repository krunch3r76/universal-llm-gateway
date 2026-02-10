"""
Async client wrapper for universal_transport.

This module provides a high-level async client interface that combines
transport and protocol layers for easy message-based communication.

Key features:
- Combines transport + protocol into simple send/receive interface
- Works with length-prefixed protocol (no readline buffer limits)
- Support for Unix and TCP transports
- Automatic connection management
- Message-level timeout handling
- Compatible with process_ipc migration patterns
"""

import asyncio
from universal_logging import get_logger
import struct
from typing import Any

from ..protocol.length_prefixed import LengthPrefixedProtocol
from ..protocol.serializers import JSONSerializer, Serializer
from ..transport.tcp_async import AsyncTCPTransport, AsyncTCPTransportError
from ..transport.unix_async import AsyncUnixTransport, AsyncUnixTransportError

logger = get_logger(__name__)


class AsyncTransportClientError(Exception):
    """Base exception for async transport client errors."""

    pass


class AsyncClientConnectionError(AsyncTransportClientError):
    """Raised when client connection fails."""

    pass


class AsyncClientSendError(AsyncTransportClientError):
    """Raised when message sending fails."""

    pass


class AsyncClientReceiveError(AsyncTransportClientError):
    """Raised when message receiving fails."""

    pass


class AsyncTransportClient:
    """
    High-level async client for message-based communication.

    Combines transport and protocol layers to provide a simple interface
    for sending and receiving messages. Designed to work optimally with
    length-prefixed protocols to avoid asyncio readline buffer limits.

    Features:
    - Message-level send/receive (not raw bytes)
    - Length-prefixed protocol (no buffer limit issues)
    - Support for Unix and TCP transports
    - Pluggable serialization formats
    - Automatic connection management
    - Timeout handling for operations
    - process_ipc compatible patterns

    Attributes:
        transport: Underlying async transport (Unix or TCP)
        protocol: Message protocol (length-prefixed)
        connected: Whether client is connected
    """

    def __init__(
        self,
        transport: AsyncUnixTransport | AsyncTCPTransport,
        protocol: LengthPrefixedProtocol | None = None,
    ):
        """
        Initialize async transport client.

        Args:
            transport: Async transport instance (Unix or TCP)
            protocol: Message protocol (default: JSON length-prefixed)
        """
        self.transport = transport
        self.protocol = protocol or LengthPrefixedProtocol(JSONSerializer())

        # Validate protocol is length-prefixed (no readline issues)
        if not isinstance(self.protocol, LengthPrefixedProtocol):
            logger.warning(
                f"Protocol {type(self.protocol)} may have readline buffer limit issues. "
                f"Consider using LengthPrefixedProtocol."
            )

        logger.debug(
            f"Async client initialized: transport={transport}, protocol={self.protocol}"
        )

    async def connect(self, timeout: float | None = None) -> None:
        """
        Connect to server.

        Args:
            timeout: Connection timeout in seconds

        Raises:
            AsyncClientConnectionError: If connection fails
        """
        try:
            if timeout:
                await asyncio.wait_for(self.transport.connect(), timeout=timeout)
            else:
                await self.transport.connect()

            logger.info(f"Client connected: {self.transport}")

        except TimeoutError:
            raise AsyncClientConnectionError(f"Connection timeout after {timeout}s")
        except (AsyncUnixTransportError, AsyncTCPTransportError) as e:
            raise AsyncClientConnectionError(f"Transport connection failed: {e}")
        except Exception as e:
            raise AsyncClientConnectionError(f"Failed to connect: {e}")

    async def send_message(self, message: Any, timeout: float | None = None) -> None:
        """
        Send a message to the server.

        Args:
            message: Message to send (format depends on protocol serializer)
            timeout: Send timeout in seconds

        Raises:
            AsyncClientSendError: If sending fails
            AsyncTransportClientError: If not connected
        """
        if not self.is_connected():
            raise AsyncTransportClientError("Client is not connected")

        try:
            # Encode message using protocol
            frame_bytes = self.protocol.encode(message)

            # Send frame over transport
            if timeout:
                await asyncio.wait_for(
                    self.transport.send(frame_bytes), timeout=timeout
                )
            else:
                await self.transport.send(frame_bytes)

            logger.debug(f"Sent message: {len(frame_bytes)} bytes")

        except TimeoutError:
            raise AsyncClientSendError(f"Send timeout after {timeout}s")
        except Exception as e:
            raise AsyncClientSendError(f"Failed to send message: {e}")

    async def receive_message(self, timeout: float | None = None) -> Any:
        """
        Receive a message from the server.

        This method uses the length-prefixed protocol to read messages
        deterministically without readline buffer limit issues.

        Args:
            timeout: Receive timeout in seconds

        Returns:
            Decoded message (format depends on protocol serializer)

        Raises:
            AsyncClientReceiveError: If receiving fails
            AsyncTransportClientError: If not connected
        """
        if not self.is_connected():
            raise AsyncTransportClientError("Client is not connected")

        try:
            # For length-prefixed protocol, we need to:
            # 1. Read 4-byte length prefix
            # 2. Read exact payload bytes
            # This avoids all readline buffer limit issues

            # Read length prefix (4 bytes)
            if timeout:
                length_bytes = await asyncio.wait_for(
                    self.transport.receive(4), timeout=timeout
                )
            else:
                length_bytes = await self.transport.receive(4)

            if len(length_bytes) != 4:
                raise AsyncClientReceiveError(
                    f"Expected 4-byte length prefix, got {len(length_bytes)} bytes"
                )

            # Decode payload length
            payload_length = struct.unpack("!I", length_bytes)[0]

            # Validate payload length
            if payload_length > self.protocol.max_message_size:
                raise AsyncClientReceiveError(
                    f"Message payload length {payload_length} exceeds maximum "
                    f"{self.protocol.max_message_size} bytes"
                )

            # Read exact payload
            if timeout:
                payload_bytes = await asyncio.wait_for(
                    self.transport.receive(payload_length), timeout=timeout
                )
            else:
                payload_bytes = await self.transport.receive(payload_length)

            if len(payload_bytes) != payload_length:
                raise AsyncClientReceiveError(
                    f"Expected {payload_length} payload bytes, got {len(payload_bytes)} bytes"
                )

            # Deserialize message
            message = self.protocol.serializer.deserialize(payload_bytes)

            logger.debug(f"Received message: {payload_length} bytes")
            return message

        except TimeoutError:
            raise AsyncClientReceiveError(f"Receive timeout after {timeout}s")
        except Exception as e:
            raise AsyncClientReceiveError(f"Failed to receive message: {e}")

    async def request_response(self, request: Any, timeout: float | None = None) -> Any:
        """
        Send request and wait for response.

        Args:
            request: Request message to send
            timeout: Total timeout for request+response

        Returns:
            Response message

        Raises:
            AsyncClientSendError: If sending request fails
            AsyncClientReceiveError: If receiving response fails
        """
        # Split timeout between send and receive
        send_timeout = timeout / 2 if timeout else None
        receive_timeout = timeout / 2 if timeout else None

        await self.send_message(request, timeout=send_timeout)
        return await self.receive_message(timeout=receive_timeout)

    async def close(self) -> None:
        """Close the client connection."""
        await self.transport.close()
        logger.info(f"Client closed: {self.transport}")

    def is_connected(self) -> bool:
        """Check if client is connected to server."""
        return self.transport.is_connected()

    def get_protocol_info(self) -> dict[str, Any]:
        """Get information about the current protocol."""
        return {
            "protocol_type": type(self.protocol).__name__,
            "serializer": str(self.protocol.serializer),
            "max_message_size": self.protocol.max_message_size,
            "state": getattr(self.protocol, "_state", None),
        }

    def get_transport_info(self) -> dict[str, Any]:
        """Get information about the current transport."""
        info = {
            "transport_type": type(self.transport).__name__,
            "connected": self.is_connected(),
        }

        # Add transport-specific info
        if isinstance(self.transport, AsyncUnixTransport):
            info["socket_path"] = str(self.transport.socket_path)
        elif isinstance(self.transport, AsyncTCPTransport):
            info["host"] = self.transport.host
            info["port"] = self.transport.port
            info["peer_address"] = self.transport.get_peer_address()
            info["local_address"] = self.transport.get_local_address()

        return info

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    def __str__(self) -> str:
        return f"AsyncTransportClient({self.transport}, {self.protocol})"

    def __repr__(self) -> str:
        return (
            f"AsyncTransportClient(transport={self.transport!r}, "
            f"protocol={self.protocol!r})"
        )


# Convenience factory functions for common configurations


async def create_unix_client(
    socket_path: str,
    serializer: Serializer | None = None,
    max_message_size: int = 4 * 1024 * 1024,
) -> AsyncTransportClient:
    """
    Create async Unix socket client with length-prefixed protocol.

    Args:
        socket_path: Path to Unix socket
        serializer: Message serializer (default: JSON)
        max_message_size: Maximum message size in bytes

    Returns:
        Configured async client (not connected)
    """
    transport = AsyncUnixTransport(socket_path)
    protocol = LengthPrefixedProtocol(
        serializer=serializer or JSONSerializer(), max_message_size=max_message_size
    )
    return AsyncTransportClient(transport, protocol)


async def create_tcp_client(
    host: str = "localhost",
    port: int = 0,
    serializer: Serializer | None = None,
    max_message_size: int = 4 * 1024 * 1024,
) -> AsyncTransportClient:
    """
    Create async TCP client with length-prefixed protocol.

    Args:
        host: Server hostname or IP
        port: Server port number
        serializer: Message serializer (default: JSON)
        max_message_size: Maximum message size in bytes

    Returns:
        Configured async client (not connected)
    """
    transport = AsyncTCPTransport(host, port)
    protocol = LengthPrefixedProtocol(
        serializer=serializer or JSONSerializer(), max_message_size=max_message_size
    )
    return AsyncTransportClient(transport, protocol)


# process_ipc migration helpers


class ProcessIPCCompatibleClient(AsyncTransportClient):
    """
    process_ipc compatible async client.

    Provides interface compatibility for migrating from process_ipc
    to universal_transport with minimal code changes.
    """

    async def send(self, message: Any, timeout: float | None = None) -> None:
        """Send message (process_ipc compatible method name)."""
        await self.send_message(message, timeout)

    async def receive(self, timeout: float | None = None) -> Any:
        """Receive message (process_ipc compatible method name)."""
        return await self.receive_message(timeout)

    async def call(self, request: Any, timeout: float | None = None) -> Any:
        """Request-response call (process_ipc compatible method name)."""
        return await self.request_response(request, timeout)


async def create_process_ipc_client(
    socket_path: str, serializer: Serializer | None = None
) -> ProcessIPCCompatibleClient:
    """
    Create process_ipc compatible client.

    Args:
        socket_path: Path to Unix socket
        serializer: Message serializer (default: JSON)

    Returns:
        process_ipc compatible client
    """
    transport = AsyncUnixTransport(socket_path)
    protocol = LengthPrefixedProtocol(serializer=serializer or JSONSerializer())
    return ProcessIPCCompatibleClient(transport, protocol)
