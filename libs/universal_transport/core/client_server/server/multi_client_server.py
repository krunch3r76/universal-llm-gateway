"""
Multi-client async transport server implementation.

This module provides the MultiClientServer class for traditional
multi-client server patterns with message handlers.
"""

import asyncio
from universal_logging import get_logger
from collections.abc import Awaitable, Callable
from typing import Any

from ...protocol.length_prefixed import LengthPrefixedProtocol
from ...transport.tcp_async import AsyncTCPClientHandler, AsyncTCPServer
from ...transport.unix_async import AsyncUnixClientHandler, AsyncUnixServer
from .base_server import BaseAsyncServer
from .session import AsyncClientSession

logger = get_logger(__name__)

# Type alias for message handler functions
MessageHandler = Callable[[Any, AsyncClientSession], Awaitable[Any]]


class MultiClientServer(BaseAsyncServer):
    """
    Async server for handling multiple clients with message handlers.

    This server implements the traditional multi-client pattern where each
    client connection is handled by a message handler function. The server
    reads messages from clients and routes them to the configured handler.

    This pattern is ideal for:
    - Request/response servers
    - Chat servers
    - Game servers
    - Any scenario with multiple concurrent clients

    Note: This server does NOT support get_transport() or MessagePump patterns.
    Use SingleClientServer for those use cases.

    Example:
        ```python
        async def handle_message(message, session):
            # Process message and return response
            return {"response": f"Echo: {message}"}

        server = MultiClientServer(
            AsyncUnixServer("/tmp/server.sock"),
            message_handler=handle_message
        )
        await server.start()
        ```
    """

    def __init__(
        self,
        server: AsyncUnixServer | AsyncTCPServer,
        protocol: LengthPrefixedProtocol | None = None,
        message_handler: MessageHandler | None = None,
    ):
        """
        Initialize multi-client server.

        Args:
            server: Async server instance (Unix or TCP)
            protocol: Message protocol (default: JSON length-prefixed)
            message_handler: Handler for incoming messages
        """
        super().__init__(server, protocol)
        self.message_handler = message_handler

        logger.debug(f"MultiClientServer initialized with handler: {message_handler}")

    async def _handle_new_client(
        self, client_handler: AsyncUnixClientHandler | AsyncTCPClientHandler
    ) -> None:
        """
        Handle new client connection.

        Creates a session for the client and starts the message handling loop.

        Args:
            client_handler: Low-level client transport handler
        """
        # Generate unique client ID
        self._client_counter += 1
        client_id = f"client_{self._client_counter}"

        # Create client session
        session = AsyncClientSession(client_handler, self.protocol, client_id)
        self.clients[client_id] = session

        logger.info(f"New client session: {client_id} (total: {len(self.clients)})")

        try:
            # Handle messages from this client
            await self._handle_client_messages(session)

        except Exception as e:
            logger.error(f"Error handling client {client_id}: {e}")
        finally:
            # Clean up client session
            if client_id in self.clients:
                del self.clients[client_id]

            try:
                await session.close()
            except Exception:
                pass

            logger.info(f"Client session ended: {client_id}")

    async def _handle_client_messages(self, session: AsyncClientSession) -> None:
        """
        Handle messages from a client session.

        Continuously reads messages from the client and routes them to the
        configured message handler.

        Args:
            session: Client session to handle
        """
        while session.is_connected() and self._running:
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

    async def send_to_client(self, client_id: str, message: Any) -> bool:
        """
        Send message to specific client.

        Args:
            client_id: ID of target client
            message: Message to send

        Returns:
            True if message was sent successfully
        """
        session = self.get_client_by_id(client_id)
        if not session:
            logger.warning(f"Client {client_id} not found")
            return False

        try:
            await session.send_message(message)
            return True
        except Exception as e:
            logger.error(f"Failed to send message to {client_id}: {e}")
            return False
