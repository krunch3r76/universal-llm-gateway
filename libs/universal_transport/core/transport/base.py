"""
Abstract base class for all transport implementations.

This module defines the Transport interface that all concrete transport
implementations must follow.
"""

from abc import ABC, abstractmethod
from enum import Enum

from universal_logging import get_logger

# Import core exceptions to avoid duplication
from ..exceptions import TransportError, UTConnectionError

logger = get_logger(__name__)


class ConnectionState(Enum):
    """Connection states for transport lifecycle."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CLOSING = "closing"
    ERROR = "error"


class SendError(TransportError):
    """Raised when sending data fails."""

    pass


class ReceiveError(TransportError):
    """Raised when receiving data fails."""

    pass


# Unix Socket Specific Exceptions
class UnixSocketConnectionError(UTConnectionError):
    """Base exception for Unix socket connection errors."""

    pass


class UnixSocketNotFoundError(UnixSocketConnectionError):
    """Raised when Unix socket file does not exist."""

    pass


class UnixSocketPermissionError(UnixSocketConnectionError):
    """Raised when Unix socket file has permission issues."""

    pass


class UnixSocketConnectionRefusedError(UnixSocketConnectionError):
    """Raised when Unix socket connection is refused by server."""

    pass


class UnixSocketInvalidError(UnixSocketConnectionError):
    """Raised when Unix socket file exists but is not a valid socket."""

    pass


# UDP Specific Exceptions
class UDPConnectionError(UTConnectionError):
    """Raised when UDP connection/binding fails."""

    pass


class UDPBindError(UDPConnectionError):
    """Raised when UDP socket cannot bind to specified address."""

    pass


class UDPSendError(SendError):
    """Raised when UDP send operation fails."""

    pass


# TCP Specific Exceptions
class TCPConnectionError(UTConnectionError):
    """Raised when TCP connection fails."""

    pass


class TCPConnectionRefusedError(TCPConnectionError):
    """Raised when TCP connection is refused."""

    pass


class TCPConnectionTimeoutError(TCPConnectionError):
    """Raised when TCP connection times out."""

    pass


class TCPHostUnreachableError(TCPConnectionError):
    """Raised when TCP host is unreachable."""

    pass


class Transport(ABC):
    """
    Abstract base class for all transport implementations.

    This class defines the interface that all transport mechanisms must implement,
    providing a consistent API for sending and receiving raw bytes across different
    communication channels (Unix sockets, TCP, UDP, etc.).

    Attributes:
        state: Current connection state
        auto_reconnect: Whether to automatically reconnect on disconnect
        max_reconnect_attempts: Maximum number of reconnection attempts
    """

    def __init__(self, auto_reconnect: bool = False, max_reconnect_attempts: int = 3):
        """
        Initialize the transport.

        Args:
            auto_reconnect: Whether to automatically reconnect on disconnect
            max_reconnect_attempts: Maximum number of reconnection attempts
        """
        self.state = ConnectionState.DISCONNECTED
        self.auto_reconnect = auto_reconnect
        self.max_reconnect_attempts = max_reconnect_attempts
        self._reconnect_attempts = 0

    @abstractmethod
    def connect(self, **kwargs) -> None:
        """
        Establish a connection.

        This method should establish the transport connection and set
        the state to CONNECTED on success.

        Args:
            **kwargs: Transport-specific connection parameters

        Raises:
            UTConnectionError: If connection establishment fails
        """
        pass

    @abstractmethod
    def send(self, data: bytes) -> int:
        """
        Send raw bytes over the transport.

        Args:
            data: Bytes to send

        Returns:
            Number of bytes sent

        Raises:
            SendError: If sending fails
            TransportError: If transport is not connected
        """
        pass

    @abstractmethod
    def receive(self, timeout: float | None = None) -> bytes:
        """
        Receive raw bytes from the transport.

        Args:
            timeout: Maximum time to wait for data (None for blocking)

        Returns:
            Received bytes (empty bytes if timeout occurs)

        Raises:
            ReceiveError: If receiving fails
            TransportError: If transport is not connected
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """
        Close the connection cleanly.

        This method should perform any necessary cleanup and set
        the state to DISCONNECTED.
        """
        pass

    def is_connected(self) -> bool:
        """Check if transport is currently connected."""
        return self.state == ConnectionState.CONNECTED

    def _handle_disconnect(self) -> None:
        """
        Handle unexpected disconnection.

        This method is called when a disconnect is detected and handles
        automatic reconnection if enabled.
        """
        self.state = ConnectionState.DISCONNECTED

        if (
            self.auto_reconnect
            and self._reconnect_attempts < self.max_reconnect_attempts
        ):
            self._reconnect_attempts += 1
            logger.info(
                f"Attempting reconnection "
                f"{self._reconnect_attempts}/{self.max_reconnect_attempts}"
            )

            try:
                self.connect()
                self._reconnect_attempts = 0  # Reset on successful reconnection
                logger.info("Reconnection successful")
            except UTConnectionError as e:
                logger.warning(
                    f"Reconnection attempt {self._reconnect_attempts} failed: {e}"
                )
                if self._reconnect_attempts >= self.max_reconnect_attempts:
                    self.state = ConnectionState.ERROR
                    logger.error("Maximum reconnection attempts reached")

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False
