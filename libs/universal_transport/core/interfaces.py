"""
Transport interface definitions.

This module defines the core transport interface for message-level communication.
This is separate from the byte-level Transport base class in core/transport/base.py.
"""

from abc import ABC, abstractmethod
from typing import Any


class Transport(ABC):
    """
    Abstract base class for transport mechanisms.

    Provides the interface for low-level transport operations including
    connection establishment, message sending/receiving, and cleanup.
    All transport implementations must be async and handle timeouts gracefully.
    """

    @abstractmethod
    async def connect(self, address: str, timeout: float = 30.0) -> bool:
        """
        Establish connection to the specified address.

        Args:
            address: Transport-specific address (e.g., socket path for Unix sockets)
            timeout: Connection timeout in seconds

        Returns:
            bool: True if connection successful, False otherwise

        Raises:
            UTConnectionError: If connection fails
            TimeoutError: If connection times out
        """
        pass

    @abstractmethod
    async def send(self, message: dict[str, Any]) -> bool:
        """
        Send a message through the transport.

        Args:
            message: Dictionary containing message data

        Returns:
            bool: True if message sent successfully, False otherwise

        Raises:
            TransportError: If send operation fails
        """
        pass

    @abstractmethod
    async def receive(self) -> dict[str, Any]:
        """
        Receive a message from the transport.

        Blocks indefinitely until a message is received or connection fails.
        This is the pure event-driven pattern - no timeouts for communication.

        Returns:
            Dict[str, Any]: Received message data

        Raises:
            TransportError: If receive operation fails or connection is lost
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """
        Close the transport connection and cleanup resources.

        Should be idempotent and safe to call multiple times.
        """
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """
        Check if transport is currently connected.

        Returns:
            bool: True if connected, False otherwise
        """
        pass


# Alias for backward compatibility during migration
IPCTransport = Transport
