"""
TCP server implementation.

This module provides multi-client TCP server with connection handling.
"""

import asyncio
from universal_logging import get_logger
import socket
from collections.abc import Callable

from .client_handler import AsyncTCPClientHandler
from .connection import AsyncTCPConnectionError

logger = get_logger(__name__)


class AsyncTCPServer:
    """
    Async TCP socket server.

    Provides multi-client TCP server with connection handling.
    Designed to work optimally with length-prefixed protocols.

    Features:
    - Multi-client support
    - IPv4 and IPv6 support
    - Connection handler callbacks
    - Automatic client management
    - Proper resource cleanup
    - No readline() usage

    Attributes:
        host: Server bind address
        port: Server port number (0 for auto-assignment)
        server: AsyncIO server instance
        clients: Set of connected client handlers
        connection_handler: Callback for new connections
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 0,
        connection_handler: Callable | None = None,
        max_clients: int = 100,
        reuse_address: bool = True,
        family: socket.AddressFamily = socket.AF_UNSPEC,
    ):
        """
        Initialize async TCP server.

        Args:
            host: Server bind address
            port: Server port number (0 for auto-assignment)
            connection_handler: Async callback for new connections
            max_clients: Maximum concurrent client connections
            reuse_address: Whether to set SO_REUSEADDR
            family: Address family (AF_INET, AF_INET6, or AF_UNSPEC for auto)
        """
        self.host = host
        self.port = port
        self.connection_handler = connection_handler
        self.max_clients = max_clients
        self.reuse_address = reuse_address
        self.family = family

        self.server: asyncio.Server | None = None
        self.clients: set = set()
        self.orphaned_writers: set = (
            set()
        )  # Track writers kept open when tasks complete
        self._running = False
        self._actual_port = port

        logger.debug(f"Async TCP server initialized: {host}:{port}")

    async def start(self) -> None:
        """
        Start the TCP server.

        Raises:
            AsyncTCPConnectionError: If server startup fails
        """
        if self._running:
            logger.warning("Server already running")
            return

        try:
            # Start async server
            self.server = await asyncio.start_server(
                self._handle_client_connection,
                host=self.host,
                port=self.port,
                family=self.family,
                reuse_address=self.reuse_address,
            )

            # Get actual port if auto-assigned
            if self.port == 0:
                sockets = self.server.sockets
                if sockets:
                    self._actual_port = sockets[0].getsockname()[1]
            else:
                self._actual_port = self.port

            self._running = True
            logger.info(f"TCP server started: {self.host}:{self._actual_port}")

        except Exception as e:
            raise AsyncTCPConnectionError(f"Failed to start TCP server: {e}")

    async def stop(self) -> None:
        """Stop the TCP server."""
        if not self._running:
            return

        self._running = False

        # Close server
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

        # Close all clients
        for client_task in list(self.clients):
            client_task.cancel()

        # Wait for clients to finish
        if self.clients:
            await asyncio.gather(*self.clients, return_exceptions=True)

        # Close orphaned writer connections that were kept open
        for writer in list(self.orphaned_writers):
            try:
                if not writer.is_closing():
                    writer.close()
                    await writer.wait_closed()
            except Exception as e:
                logger.debug(f"Error closing orphaned writer: {e}")

        self.orphaned_writers.clear()

        logger.info(f"TCP server stopped: {self.host}:{self._actual_port}")

    async def _handle_client_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """
        Handle a new client connection.

        Args:
            reader: AsyncIO stream reader for client
            writer: AsyncIO stream writer for client
        """
        if len(self.clients) >= self.max_clients:
            peername = writer.get_extra_info("peername")
            logger.warning(
                f"Max clients ({self.max_clients}) reached, rejecting {peername}"
            )
            writer.close()
            await writer.wait_closed()
            return

        # Create client handler task
        client_task = asyncio.create_task(self._client_handler(reader, writer))
        self.clients.add(client_task)

        # Remove completed tasks
        client_task.add_done_callback(lambda t: self.clients.discard(t))

        peername = writer.get_extra_info("peername")
        logger.info(
            f"New client connected from {peername} (total: {len(self.clients)})"
        )

    async def _client_handler(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """
        Handle individual client connection.

        Args:
            reader: AsyncIO stream reader for client
            writer: AsyncIO stream writer for client
        """
        peername = writer.get_extra_info("peername")
        keep_open = False

        try:
            # Create client transport wrapper
            client_transport = AsyncTCPClientHandler(reader, writer)

            # Call user-provided connection handler if available
            if self.connection_handler:
                keep_open = await self.connection_handler(client_transport)
            else:
                # Default: just keep connection alive
                await client_transport.wait_for_close()

        except Exception as e:
            logger.error(f"Error in client handler for {peername}: {e}")
        finally:
            # Ensure client is closed, unless transport mode requires it to stay open
            if not keep_open:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

                logger.debug(f"Client connection closed: {peername}")
            else:
                # Track orphaned writer so it can be cleaned up during stop()
                self.orphaned_writers.add(writer)
                logger.debug(
                    f"Keeping client connection open for transport mode: {peername}"
                )

    def get_client_count(self) -> int:
        """Get current number of connected clients."""
        return len(self.clients)

    def remove_orphaned_writer(self, writer: asyncio.StreamWriter) -> None:
        """Remove a writer from orphaned tracking when it's closed externally."""
        self.orphaned_writers.discard(writer)

    def is_running(self) -> bool:
        """Check if server is running."""
        return self._running and self.server is not None

    def get_actual_port(self) -> int:
        """Get the actual port number (useful when port=0 for auto-assignment)."""
        return self._actual_port

    def get_server_address(self) -> tuple[str, int]:
        """Get server address."""
        return (self.host, self._actual_port)
