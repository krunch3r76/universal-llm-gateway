"""
Client session for async transport server.

This module provides the AsyncClientSession class that represents
individual client connections with message-level interface.
"""

import asyncio
import struct
from typing import Any

from universal_logging import get_logger

from ...protocol.length_prefixed import LengthPrefixedProtocol
from ...transport.tcp_async import AsyncTCPClientHandler
from ...transport.unix_async import AsyncUnixClientHandler
from ...transport.tcp.connection import AsyncTCPReceiveError
from ...transport.unix.connection import AsyncUnixReceiveError

logger = get_logger(__name__)


class AsyncClientSession:
    """
    Represents a client session in AsyncTransportServer.

    Provides message-level interface for individual client connections.
    Each client gets its own session instance for isolated communication.

    Attributes:
        client_handler: Low-level client transport handler
        protocol: Message protocol instance
        client_id: Unique identifier for this client
        metadata: Custom metadata for this session
    """

    def __init__(
        self,
        client_handler: AsyncUnixClientHandler | AsyncTCPClientHandler,
        protocol: LengthPrefixedProtocol,
        client_id: str,
    ):
        """
        Initialize client session.

        Args:
            client_handler: Low-level client transport handler
            protocol: Message protocol instance
            client_id: Unique identifier for this client
        """
        self.client_handler = client_handler
        self.protocol = protocol
        self.client_id = client_id
        self.metadata: dict[str, Any] = {}

        # Get client address for logging
        self.peer_address = (
            getattr(client_handler, "peername", None)
            or getattr(client_handler, "get_peer_address", lambda: None)()
        )

        logger.debug(f"Client session created: {client_id} from {self.peer_address}")

    async def send_message(self, message: Any, timeout: float | None = None) -> None:
        """
        Send message to client.

        Args:
            message: Message to send
            timeout: Send timeout in seconds

        Raises:
            Exception: If sending fails
        """
        try:
            # Encode message using protocol
            frame_bytes = self.protocol.encode(message)

            # Send frame to client
            if timeout:
                await asyncio.wait_for(
                    self.client_handler.send(frame_bytes), timeout=timeout
                )
            else:
                await self.client_handler.send(frame_bytes)

            logger.debug(f"Sent message to {self.client_id}: {len(frame_bytes)} bytes")

        except Exception as e:
            logger.error(f"Failed to send message to {self.client_id}: {e}")
            raise

    async def receive_message(self, timeout: float | None = None) -> Any:
        """
        Receive message from client.

        Args:
            timeout: Receive timeout in seconds

        Returns:
            Decoded message

        Raises:
            Exception: If receiving fails
        """
        try:
            # Read length prefix (4 bytes)
            if timeout:
                length_bytes = await asyncio.wait_for(
                    self.client_handler.receive(4), timeout=timeout
                )
            else:
                length_bytes = await self.client_handler.receive(4)

            if len(length_bytes) != 4:
                raise ValueError(
                    f"Expected 4-byte length prefix, got {len(length_bytes)} bytes"
                )

            # Decode payload length
            payload_length = struct.unpack("!I", length_bytes)[0]

            # Validate payload length
            if payload_length > self.protocol.max_message_size:
                raise ValueError(
                    f"Message payload length {payload_length} exceeds maximum "
                    f"{self.protocol.max_message_size} bytes"
                )

            # Read exact payload
            if timeout:
                payload_bytes = await asyncio.wait_for(
                    self.client_handler.receive(payload_length), timeout=timeout
                )
            else:
                payload_bytes = await self.client_handler.receive(payload_length)

            if len(payload_bytes) != payload_length:
                raise ValueError(
                    f"Expected {payload_length} payload bytes, got {len(payload_bytes)} bytes"
                )

            # Deserialize message
            message = self.protocol.serializer.deserialize(payload_bytes)

            logger.debug(
                f"Received message from {self.client_id}: {payload_length} bytes"
            )
            return message

        except (AsyncTCPReceiveError, AsyncUnixReceiveError) as e:
            # Normal disconnect - client closed connection
            # Check if it's a normal disconnect (IncompleteReadError) vs actual error
            error_msg = str(e)
            if "disconnected" in error_msg.lower() or "closed" in error_msg.lower():
                logger.debug(f"Client {self.client_id} disconnected normally: {error_msg}")
            else:
                # Unexpected receive error (protocol violation, etc.)
                logger.error(f"Failed to receive message from {self.client_id}: {e}")
            raise
        except Exception as e:
            # Unexpected error (serialization, protocol violation, etc.)
            logger.error(f"Failed to receive message from {self.client_id}: {e}")
            raise

    async def close(self) -> None:
        """Close client session."""
        await self.client_handler.close()
        logger.debug(f"Client session closed: {self.client_id}")

    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self.client_handler.is_connected()

    def get_info(self) -> dict[str, Any]:
        """Get client session info."""
        return {
            "client_id": self.client_id,
            "peer_address": self.peer_address,
            "connected": self.is_connected(),
            "metadata": self.metadata.copy(),
        }
