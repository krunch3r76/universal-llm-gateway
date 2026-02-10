"""
Abstract base class for event transports.

Defines the interface that all transport implementations must follow.
All methods are async for pure async operation.
"""

from abc import ABC, abstractmethod
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


class EventTransport(ABC):
    """
    Abstract base class for event transports.

    All transport implementations (Unix socket, UDP, TCP) must implement
    these methods to be compatible with TransportServer.

    Thread Safety: Not needed. All transports operate in single async context.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """
        Initialize the transport with optional configuration.

        Args:
            config: Transport-specific configuration dict
        """
        self.config = config or {}
        self.enabled = True
        self._started = False

    @abstractmethod
    async def start(self):
        """
        Start the transport (create sockets, bind ports, etc.).

        Should be idempotent - calling multiple times should be safe.

        Raises:
            Exception: If transport fails to start
        """
        pass

    @abstractmethod
    async def stop(self):
        """
        Stop the transport and clean up resources.

        Should be idempotent - calling multiple times should be safe.
        """
        pass

    @abstractmethod
    async def send_event(self, event_data: dict[str, Any]) -> bool:
        """
        Send an event through this transport.

        Args:
            event_data: Event dictionary to send (will be JSON-encoded)

        Returns:
            True if event was sent successfully, False otherwise

        Note:
            Implementations should NOT raise exceptions - catch and log errors,
            then return False. This ensures one transport failure doesn't
            affect others.
        """
        pass

    @property
    def is_started(self) -> bool:
        """Check if transport has been started"""
        return self._started

    @property
    def transport_name(self) -> str:
        """Return a human-readable name for this transport"""
        return self.__class__.__name__
