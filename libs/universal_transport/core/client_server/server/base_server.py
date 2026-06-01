"""
Base async transport server implementation.

This module provides the BaseAsyncServer abstract class that contains
common functionality for both multi-client and single-client server patterns.
"""

from abc import ABC, abstractmethod
from typing import Any

from universal_logging import get_logger

from ...protocol.length_prefixed import LengthPrefixedProtocol
from ...protocol.serializers import JSONSerializer
from ...transport.tcp_async import AsyncTCPClientHandler, AsyncTCPServer
from ...transport.unix_async import AsyncUnixClientHandler, AsyncUnixServer
from .session import AsyncClientSession

logger = get_logger(__name__)


class AsyncTransportServerError(Exception):
    """Base exception for async transport server errors."""

    pass


class AsyncServerStartError(AsyncTransportServerError):
    """Raised when server startup fails."""

    pass


class AsyncServerStopError(AsyncTransportServerError):
    """Raised when server shutdown fails."""

    pass


class BaseAsyncServer(ABC):
    """
    Abstract base class for async transport servers.

    Provides common functionality for server implementations while allowing
    different patterns (multi-client vs single-client) to be implemented
    in subclasses.

    Attributes:
        server: Underlying async server (Unix or TCP)
        protocol: Message protocol (length-prefixed)
        clients: Active client sessions
    """

    def __init__(
        self,
        server: AsyncUnixServer | AsyncTCPServer,
        protocol: LengthPrefixedProtocol | None = None,
    ):
        """
        Initialize base async server.

        Args:
            server: Async server instance (Unix or TCP)
            protocol: Message protocol (default: JSON length-prefixed)
        """
        self.server = server
        self.protocol = protocol or LengthPrefixedProtocol(JSONSerializer())

        # Client management
        self.clients: dict[str, AsyncClientSession] = {}
        self._client_counter = 0
        self._running = False

        # Set connection handler on underlying server
        self.server.connection_handler = self._handle_new_client

        logger.debug(
            f"{self.__class__.__name__} initialized: server={server}, "
            f"protocol={self.protocol}"
        )

    async def start(self) -> None:
        """
        Start the server.

        Raises:
            AsyncServerStartError: If server startup fails
        """
        if self._running:
            logger.warning("Server already running")
            return

        try:
            await self.server.start()
            self._running = True

            logger.info(f"Server started: {self.get_server_info()}")

        except Exception as e:
            raise AsyncServerStartError(f"Failed to start server: {e}")

    async def stop(self) -> None:
        """
        Stop the server.

        Raises:
            AsyncServerStopError: If server shutdown fails
        """
        if not self._running:
            return

        try:
            self._running = False

            # Close all client sessions
            for client_id, session in list(self.clients.items()):
                try:
                    await session.close()
                except Exception as e:
                    logger.warning(f"Error closing client {client_id}: {e}")

            self.clients.clear()

            # Stop underlying server
            await self.server.stop()

            logger.info("Server stopped")

        except Exception as e:
            raise AsyncServerStopError(f"Failed to stop server: {e}")

    @abstractmethod
    async def _handle_new_client(
        self, client_handler: AsyncUnixClientHandler | AsyncTCPClientHandler
    ) -> None:
        """
        Handle new client connection.

        Must be implemented by subclasses to define client handling behavior.

        Args:
            client_handler: Low-level client transport handler
        """
        pass

    def get_client_count(self) -> int:
        """Get number of connected clients."""
        return len(self.clients)

    def get_client_sessions(self) -> list[AsyncClientSession]:
        """Get list of active client sessions."""
        return list(self.clients.values())

    def get_client_by_id(self, client_id: str) -> AsyncClientSession | None:
        """Get client session by ID."""
        return self.clients.get(client_id)

    def is_running(self) -> bool:
        """Check if server is running."""
        return self._running and self.server.is_running()

    def get_server_info(self) -> dict[str, Any]:
        """Get server information."""
        from ...transport.tcp_async import AsyncTCPServer
        from ...transport.unix_async import AsyncUnixServer

        info = {
            "server_type": type(self.server).__name__,
            "server_class": self.__class__.__name__,
            "protocol": str(self.protocol),
            "running": self.is_running(),
            "client_count": self.get_client_count(),
        }

        # Add server-specific info
        if isinstance(self.server, AsyncUnixServer):
            info["socket_path"] = str(self.server.socket_path)
        elif isinstance(self.server, AsyncTCPServer):
            info["address"] = self.server.get_server_address()

        return info

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.server}, {self.protocol})"

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(server={self.server!r}, "
            f"protocol={self.protocol!r})"
        )
