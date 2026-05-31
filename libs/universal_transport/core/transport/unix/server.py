"""
Unix socket server implementation.

This module provides multi-client Unix socket server with connection handling.
"""

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

from universal_logging import get_logger

from .client_handler import AsyncUnixClientHandler
from .connection import AsyncUnixConnectionError

logger = get_logger(__name__)


class AsyncUnixServer:
    """
    Async Unix domain socket server.

    Provides multi-client Unix socket server with connection handling.
    Designed to work optimally with length-prefixed protocols.

    Features:
    - Multi-client support
    - Connection handler callbacks
    - Automatic client management
    - Proper resource cleanup
    - No readline() usage

    Attributes:
        socket_path: Path to Unix socket file
        server: AsyncIO server instance
        clients: Set of connected client handlers
        connection_handler: Callback for new connections
    """

    def __init__(
        self,
        socket_path: str,
        connection_handler: Callable | None = None,
        max_clients: int = 100,
    ):
        """
        Initialize async Unix server.

        Args:
            socket_path: Path to Unix socket file
            connection_handler: Async callback for new connections
            max_clients: Maximum concurrent client connections
        """
        self.socket_path = Path(socket_path)
        self.connection_handler = connection_handler
        self.max_clients = max_clients

        self.server: asyncio.Server | None = None
        self.clients: set = set()
        self.orphaned_writers: set = (
            set()
        )  # Track writers kept open when tasks complete
        self._running = False

        logger.debug(f"Async Unix server initialized: {self.socket_path}")

    async def start(self) -> None:
        """
        Start the Unix socket server.

        Raises:
            AsyncUnixConnectionError: If server startup fails
        """
        if self._running:
            logger.warning("Server already running")
            return

        try:
            # Remove existing socket file
            if self.socket_path.exists():
                self.socket_path.unlink()
                logger.debug(f"Removed existing socket file: {self.socket_path}")

            # Start async server
            self.server = await asyncio.start_unix_server(
                self._handle_client_connection, str(self.socket_path)
            )

            # Set socket permissions
            os.chmod(str(self.socket_path), 0o666)

            self._running = True
            logger.info(f"Unix server started: {self.socket_path}")

        except Exception as e:
            raise AsyncUnixConnectionError(f"Failed to start Unix server: {e}")

    async def stop(self) -> None:
        """Stop the Unix socket server."""
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

        # Remove socket file
        try:
            if self.socket_path.exists():
                self.socket_path.unlink()
        except Exception as e:
            logger.warning(f"Failed to remove socket file: {e}")

        logger.info(f"Unix server stopped: {self.socket_path}")

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
            logger.warning(
                f"Max clients ({self.max_clients}) reached, rejecting connection"
            )
            writer.close()
            await writer.wait_closed()
            return

        # Create client handler task
        client_task = asyncio.create_task(self._client_handler(reader, writer))
        self.clients.add(client_task)

        # Remove completed tasks
        client_task.add_done_callback(lambda t: self.clients.discard(t))

        logger.info(f"New client connected (total: {len(self.clients)})")

    async def _client_handler(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """
        Handle individual client connection.

        Args:
            reader: AsyncIO stream reader for client
            writer: AsyncIO stream writer for client
        """
        keep_open = False
        try:
            # Create client transport wrapper
            client_transport = AsyncUnixClientHandler(reader, writer)

            # Call user-provided connection handler if available
            if self.connection_handler:
                keep_open = await self.connection_handler(client_transport)
            else:
                # Default: just keep connection alive
                await client_transport.wait_for_close()

        except Exception as e:
            logger.error(f"Error in client handler: {e}")
        finally:
            # Ensure client is closed, unless transport mode requires it to stay open
            if not keep_open:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

                logger.debug("Client connection closed")
            else:
                # Track orphaned writer so it can be cleaned up during stop()
                self.orphaned_writers.add(writer)
                logger.debug("Keeping client connection open for transport mode.")

    def get_client_count(self) -> int:
        """Get current number of connected clients."""
        return len(self.clients)

    def remove_orphaned_writer(self, writer: asyncio.StreamWriter) -> None:
        """Remove a writer from orphaned tracking when it's closed externally."""
        self.orphaned_writers.discard(writer)

    def is_running(self) -> bool:
        """Check if server is running."""
        return self._running and self.server is not None
