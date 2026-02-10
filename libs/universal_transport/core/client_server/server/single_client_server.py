"""
Single-client async transport server implementation.

This module provides the SingleClientServer class for single-client
scenarios that need Transport/MessagePump compatibility.
"""

import asyncio
from universal_logging import get_logger
from ...interfaces import Transport
from ...message_pump import MessagePump
from ...protocol.length_prefixed import LengthPrefixedProtocol
from ...transport.tcp_async import AsyncTCPClientHandler, AsyncTCPServer
from ...transport.unix_async import AsyncUnixClientHandler, AsyncUnixServer
from .base_server import BaseAsyncServer
from .server_impl import ServerSessionTransport
from .session import AsyncClientSession

logger = get_logger(__name__)


class SingleClientServer(BaseAsyncServer):
    """
    Async server for single-client scenarios with Transport/MessagePump support.

    This server is designed for scenarios where only one client connects and
    the server needs to use Transport-based patterns like MessagePump. The
    server does NOT use message handlers - instead, it provides a Transport
    interface that can be used with MessagePump or similar utilities.

    This pattern is ideal for:
    - Worker processes
    - Dedicated service connections
    - Point-to-point communication
    - Integration with MessagePump for correlation matching

    Note: This server does NOT support message handlers. Use MultiClientServer
    for traditional server patterns with multiple clients.

    Example:
        ```python
        server = SingleClientServer(AsyncUnixServer("/tmp/worker.sock"))
        await server.start()

        # Wait for client and get transport
        transport = await server.get_transport()

        # Use with MessagePump
        pump = MessagePump(transport)
        await pump.start()

        # Or create MessagePump directly
        pump = await server.create_message_pump()
        ```
    """

    def __init__(
        self,
        server: AsyncUnixServer | AsyncTCPServer,
        protocol: LengthPrefixedProtocol | None = None,
    ):
        """
        Initialize single-client server.

        Args:
            server: Async server instance (Unix or TCP)
            protocol: Message protocol (default: JSON length-prefixed)
        """
        super().__init__(server, protocol)

        # Track if we're waiting for a client
        self._transport_session: AsyncClientSession | None = None
        self._transport_ready = asyncio.Event()

        logger.debug("SingleClientServer initialized for Transport/MessagePump pattern")

    async def _handle_new_client(
        self, client_handler: AsyncUnixClientHandler | AsyncTCPClientHandler
    ) -> None:
        """
        Handle new client connection.

        For single-client server, we don't start a message handler.
        Instead, we store the session for Transport access.

        Args:
            client_handler: Low-level client transport handler
        """
        # Generate unique client ID
        self._client_counter += 1
        client_id = f"client_{self._client_counter}"

        # Create client session
        session = AsyncClientSession(client_handler, self.protocol, client_id)

        # Check if we already have a client
        if self._transport_session is not None:
            logger.warning(
                f"Rejecting additional client {client_id} - single-client mode"
            )
            try:
                await session.close()
            except Exception:
                pass
            return

        # Store as our single client
        self.clients[client_id] = session
        self._transport_session = session
        self._transport_ready.set()

        logger.info(f"Single client connected: {client_id}")

        # Keep session alive until it disconnects
        # We don't read messages here - that's handled by Transport/MessagePump
        try:
            while session.is_connected() and self._running:
                await asyncio.sleep(1.0)  # Just monitor connection
        except Exception as e:
            logger.debug(f"Client {client_id} monitoring ended: {e}")
        finally:
            # Clean up on disconnect
            if client_id in self.clients:
                del self.clients[client_id]

            if self._transport_session == session:
                self._transport_session = None
                self._transport_ready.clear()

            logger.info(f"Single client disconnected: {client_id}")

    async def get_transport(self, timeout: float = 30.0) -> Transport:
        """
        Get a Transport interface for the single client.

        Waits for a client to connect (if not already connected) and returns
        a Transport adapter for that client's session.

        Args:
            timeout: Maximum time to wait for client connection

        Returns:
            Transport adapter for the client

        Raises:
            TimeoutError: If no client connects within timeout

        Example:
            ```python
            transport = await server.get_transport(timeout=30.0)
            # Use transport directly or with MessagePump
            ```
        """
        if not self._running:
            await self.start()

        # Wait for client to connect
        try:
            await asyncio.wait_for(self._transport_ready.wait(), timeout=timeout)
        except TimeoutError:
            raise TimeoutError(f"No client connected within {timeout}s")

        if self._transport_session is None:
            raise RuntimeError("Transport session was cleared unexpectedly")

        logger.debug(
            f"Providing transport for session: {self._transport_session.client_id}"
        )
        return ServerSessionTransport(self._transport_session, self)

    async def create_message_pump(self, **kwargs) -> MessagePump:
        """
        Create a MessagePump for the single client.

        This is a convenience method that gets the transport and creates
        a MessagePump in one step.

        Args:
            **kwargs: Additional arguments passed to MessagePump constructor

        Returns:
            Configured MessagePump ready to use

        Example:
            ```python
            pump = await server.create_message_pump()
            await pump.start()

            # Send requests with correlation
            response = await pump.send_request({"command": "status"})
            ```
        """
        transport = await self.get_transport()
        pump = MessagePump(transport, **kwargs)
        logger.debug("Created MessagePump for single-client server")
        return pump

    async def wait_for_client(self, timeout: float = 30.0) -> None:
        """
        Wait for a client to connect.

        Args:
            timeout: Maximum time to wait

        Raises:
            TimeoutError: If no client connects within timeout
        """
        if not self._running:
            await self.start()

        try:
            await asyncio.wait_for(self._transport_ready.wait(), timeout=timeout)
        except TimeoutError:
            raise TimeoutError(f"No client connected within {timeout}s")

    def is_client_connected(self) -> bool:
        """Check if a client is currently connected."""
        return (
            self._transport_session is not None
            and self._transport_session.is_connected()
        )

    def get_client_session(self) -> AsyncClientSession | None:
        """Get the single client session if connected."""
        return self._transport_session

    async def stop(self) -> None:
        """Stop the server and clean up."""
        # Clear transport session reference
        self._transport_session = None
        self._transport_ready.clear()

        # Call parent stop
        await super().stop()
