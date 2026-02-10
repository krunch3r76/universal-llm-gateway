"""
Async transport server implementation.

This module provides the AsyncTransportServer class that combines
transport and protocol layers for message-based multi-client handling.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from universal_logging import get_logger

from ...exceptions import TransportError
from ...interfaces import Transport
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


# Type alias for message handler functions
MessageHandler = Callable[[Any, AsyncClientSession], Awaitable[Any]]


class ServerSessionTransport(Transport):
    """
    Transport adapter for a single client session from AsyncTransportServer.

    Wraps an AsyncClientSession to provide Transport interface compatibility.
    This enables single-client servers to work with Transport-based utilities
    like MessagePump while using the multi-client server infrastructure.
    """

    def __init__(self, session: AsyncClientSession, server: "AsyncTransportServer"):
        """
        Initialize server session transport.

        Args:
            session: Client session to wrap
            server: Parent server instance
        """
        self._session = session
        self._server = server
        self._connected = True

        logger.debug(f"ServerSessionTransport created for session: {session.client_id}")

    async def connect(self, address: str = None, timeout: float = 30.0) -> bool:
        """
        Already connected via session.

        Args:
            address: Ignored (session already connected)
            timeout: Ignored (session already connected)

        Returns:
            bool: True if session is connected
        """
        return self._session.is_connected()

    async def send(self, message: dict[str, Any]) -> bool:
        """
        Send message to client session.

        Args:
            message: Message dictionary to send

        Returns:
            bool: True if message sent successfully

        Raises:
            TransportError: If session not connected or send fails
        """
        if not self._session.is_connected():
            raise TransportError("Session not connected")

        try:
            await self._session.send_message(message)
            return True
        except Exception as e:
            raise TransportError(f"Failed to send message: {e}") from e

    async def receive(self) -> dict[str, Any]:
        """
        Receive message from client session.

        Returns:
            Dict[str, Any]: Received message

        Raises:
            TransportError: If session not connected or receive fails
        """
        if not self._session.is_connected():
            raise TransportError("Session not connected")

        try:
            return await self._session.receive_message()
        except Exception as e:
            raise TransportError(f"Failed to receive message: {e}") from e

    async def close(self) -> None:
        """Close session connection."""
        if self._connected:
            await self._session.close()
            self._connected = False
            logger.debug(
                f"ServerSessionTransport closed for session: {self._session.client_id}"
            )

    def is_connected(self) -> bool:
        """Check if session is connected."""
        return self._connected and self._session.is_connected()


class AsyncTransportServer:
    """
    High-level async server for message-based multi-client communication.

    Combines transport and protocol layers to provide a simple interface
    for handling multiple clients with message-based communication.
    Designed to work optimally with length-prefixed protocols.

    Features:
    - Multi-client message handling
    - Length-prefixed protocol (no buffer limit issues)
    - Support for Unix and TCP transports
    - Pluggable message handlers
    - Automatic client session management
    - process_ipc compatible patterns

    Attributes:
        server: Underlying async server (Unix or TCP)
        protocol: Message protocol (length-prefixed)
        message_handler: Handler function for incoming messages
        clients: Active client sessions
    """

    def __init__(
        self,
        server: AsyncUnixServer | AsyncTCPServer,
        protocol: LengthPrefixedProtocol | None = None,
        message_handler: MessageHandler | None = None,
    ):
        """
        Initialize async transport server.

        Args:
            server: Async server instance (Unix or TCP)
            protocol: Message protocol (default: JSON length-prefixed)
            message_handler: Handler for incoming messages
        """
        self.server = server
        self.protocol = protocol or LengthPrefixedProtocol(JSONSerializer())
        self.message_handler = message_handler

        # Client management
        self.clients: dict[str, AsyncClientSession] = {}
        self._client_counter = 0
        self._running = False
        self._transport_sessions: set[str] = (
            set()
        )  # Sessions managed by get_transport()
        self._get_transport_waiting = False  # Track if get_transport() is waiting

        # Set connection handler on underlying server
        self.server.connection_handler = self._handle_new_client

        logger.debug(
            f"Async server initialized: server={server}, protocol={self.protocol}"
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

    async def _handle_new_client(
        self, client_handler: AsyncUnixClientHandler | AsyncTCPClientHandler
    ) -> bool:
        """
        Handle new client connection.

        Args:
            client_handler: Low-level client transport handler

        Returns:
            bool: True if the transport connection should be kept open for get_transport().
        """
        # Generate unique client ID
        self._client_counter += 1
        client_id = f"client_{self._client_counter}"

        # Create client session
        session = AsyncClientSession(client_handler, self.protocol, client_id)
        self.clients[client_id] = session

        logger.info(f"New client session: {client_id} (total: {len(self.clients)})")

        try:
            # Don't start message handler if get_transport() is waiting
            if self._get_transport_waiting:
                logger.debug(
                    f"get_transport() is waiting, skipping message handler for {client_id}"
                )
                # Don't start _handle_client_messages() - get_transport() will handle it
                # ✅ Session remains open for get_transport() to retrieve
                # Don't close session here - let get_transport() manage it
            elif session.client_id not in self._transport_sessions:
                # Normal multi-client mode: handle messages
                await self._handle_client_messages(session)

        except Exception as e:
            logger.error(f"Error handling client {client_id}: {e}")
            # Only close on error if session shouldn't be kept open for get_transport()
            should_close_on_error = True

            if (
                self._get_transport_waiting
                and session.client_id not in self._transport_sessions
            ):
                # Check if this is the first session that get_transport() will use
                sessions = list(self.clients.values())
                is_first_session = (
                    len(sessions) == 1 and sessions[0].client_id == client_id
                )
                should_close_on_error = not is_first_session
            elif session.client_id in self._transport_sessions:
                # Session already handed off to get_transport()
                should_close_on_error = False

            if should_close_on_error:
                try:
                    await session.close()
                except Exception:
                    pass
        finally:
            # Only clean up if session wasn't used by get_transport()
            # When get_transport() is waiting, only the FIRST session should be left open
            should_keep_open = False

            if (
                self._get_transport_waiting
                and session.client_id not in self._transport_sessions
            ):
                # Check if this is the first session that get_transport() will use
                sessions = list(self.clients.values())
                is_first_session = (
                    len(sessions) == 1 and sessions[0].client_id == client_id
                )
                should_keep_open = is_first_session
            elif session.client_id in self._transport_sessions:
                # Session already handed off to get_transport()
                should_keep_open = True

            if not should_keep_open:
                # Normal cleanup: remove from clients dict and close session
                if client_id in self.clients:
                    del self.clients[client_id]

                try:
                    if not session.is_closed():
                        await session.close()
                except Exception:
                    pass

                logger.info(f"Client session ended: {client_id}")
            else:
                # Keep session open for get_transport()
                logger.debug(f"Leaving session {client_id} open for get_transport()")

        # Only keep connection open if this is the FIRST session when get_transport() is waiting
        # get_transport() only uses the first session, so others should be closed normally
        if self._get_transport_waiting:
            sessions = list(self.clients.values())
            is_first_session = len(sessions) == 1 and sessions[0].client_id == client_id
            return is_first_session

        return False

    async def _handle_client_messages(self, session: AsyncClientSession) -> None:
        """
        Handle messages from a client session.

        Args:
            session: Client session to handle
        """
        while session.is_connected() and self._running:
            # Gracefully exit if this session is being handed off to get_transport()
            if session.client_id in self._transport_sessions:
                logger.debug(
                    f"Session {session.client_id} taken over by get_transport(). Stopping message handler."
                )
                break

            try:
                # Receive message from client
                message = await session.receive_message(
                    timeout=1.0
                )  # 1s timeout to check _running

                # Call user message handler if provided
                if self.message_handler:
                    try:
                        # Call handler and optionally get response
                        response = await self.message_handler(message, session)

                        # Send response if handler returned one
                        if response is not None:
                            await session.send_message(response)

                    except Exception as e:
                        logger.error(
                            f"Error in message handler for {session.client_id}: {e}"
                        )
                        # Optionally send error response
                        try:
                            error_response = {
                                "error": f"Message handler error: {e}",
                                "original_message": message,
                            }
                            await session.send_message(error_response)
                        except Exception:
                            pass  # Don't fail if error response fails
                else:
                    # No handler - echo message back (default behavior)
                    await session.send_message(message)

            except TimeoutError:
                # Timeout is expected - just check if we should continue
                continue
            except Exception as e:
                logger.debug(f"Client {session.client_id} disconnected: {e}")
                break

    def broadcast_message(self, message: Any) -> int:
        """
        Broadcast message to all connected clients.

        Args:
            message: Message to broadcast

        Returns:
            Number of clients that received the message
        """
        sent_count = 0

        for client_id, session in self.clients.items():
            try:
                # Create task with exception handler to prevent "never retrieved" errors
                task = asyncio.create_task(session.send_message(message))
                task.add_done_callback(
                    lambda t, cid=client_id: self._handle_broadcast_result(t, cid)
                )
                sent_count += 1
            except Exception as e:
                logger.warning(f"Failed to create broadcast task for {client_id}: {e}")

        logger.debug(f"Broadcast message to {sent_count} clients")
        return sent_count

    def _handle_broadcast_result(self, task: asyncio.Task, client_id: str) -> None:
        """
        Handle broadcast task completion and cleanup failed clients.

        Args:
            task: Completed broadcast task
            client_id: ID of client that was sent to
        """
        try:
            # Retrieve exception if one occurred
            task.result()
        except Exception as e:
            # Log and clean up disconnected client
            logger.debug(
                f"Broadcast to {client_id} failed (client likely disconnected): {e}"
            )
            if client_id in self.clients:
                del self.clients[client_id]

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

    async def get_transport(self, timeout: float = 30.0) -> Transport:
        """
        Get a Transport-compatible handle for the first (single) client.

        This method waits for the first client to connect and returns a Transport
        adapter that wraps that client's session. Useful for single-client scenarios
        where you need Transport interface compatibility (e.g., with MessagePump).

        **Use Cases:**
        - Single-client server patterns (worker processes, dedicated services)
        - Integration with Transport-based utilities (MessagePump, correlation matching)
        - Migration from single-connection APIs to multi-client servers

        Args:
            timeout: Maximum time to wait for client connection

        Returns:
            Transport adapter for the first client

        Raises:
            ValueError: If multiple clients are connected (use get_client_sessions() instead)
            TimeoutError: If no client connects within timeout

        Example:
            ```python
            server = AsyncTransportServer(...)
            await server.start()

            # Wait for single client and get Transport interface
            transport = await server.get_transport(timeout=30.0)

            # Use with MessagePump or other Transport-based utilities
            pump = MessagePump(transport)
            ```
        """
        # Issue deprecation warning
        import warnings

        warnings.warn(
            "AsyncTransportServer.get_transport() mixes incompatible patterns and will be deprecated. "
            "Consider using SingleClientServer instead for single-client scenarios with MessagePump support. "
            "See universal_transport.core.client_server.server.SingleClientServer for the recommended approach.",
            DeprecationWarning,
            stacklevel=2,
        )

        if not self._running:
            await self.start()

        # Set flag BEFORE waiting to prevent message handler from starting
        self._get_transport_waiting = True

        try:
            start_time = asyncio.get_event_loop().time()

            while (asyncio.get_event_loop().time() - start_time) < timeout:
                sessions = self.get_client_sessions()

                if len(sessions) > 1:
                    raise ValueError(
                        "Multiple clients connected. get_transport() only supports single-client mode. "
                        "Use get_client_sessions() instead or set max_clients=1 on the underlying server."
                    )

                if sessions:
                    session = sessions[0]
                    self._transport_sessions.add(session.client_id)
                    logger.debug(
                        f"get_transport() is handing off session {session.client_id}"
                    )
                    return ServerSessionTransport(session, self)

                await asyncio.sleep(0.1)

            raise TimeoutError(f"No client connected within {timeout}s")
        finally:
            # Always clear the flag when done
            self._get_transport_waiting = False

    def get_server_info(self) -> dict[str, Any]:
        """Get server information."""
        from ...transport.tcp_async import AsyncTCPServer
        from ...transport.unix_async import AsyncUnixServer

        info = {
            "server_type": type(self.server).__name__,
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
        return f"AsyncTransportServer({self.server}, {self.protocol})"

    def __repr__(self) -> str:
        return (
            f"AsyncTransportServer(server={self.server!r}, protocol={self.protocol!r})"
        )
