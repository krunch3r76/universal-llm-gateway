"""
Factory functions for creating async transport servers.

This module provides convenience functions for creating configured
server instances and process_ipc compatibility helpers.
"""

from typing import Any

from ...protocol.length_prefixed import LengthPrefixedProtocol
from ...protocol.serializers import JSONSerializer, Serializer
from ...transport.tcp_async import AsyncTCPServer
from ...transport.unix_async import AsyncUnixServer
from .multi_client_server import MultiClientServer
from .server_impl import AsyncTransportServer, MessageHandler
from .single_client_server import SingleClientServer


async def create_unix_server(
    socket_path: str,
    message_handler: MessageHandler | None = None,
    serializer: Serializer | None = None,
    max_message_size: int = 4 * 1024 * 1024,
    max_clients: int = 100,
) -> AsyncTransportServer:
    """
    Create async Unix socket server with length-prefixed protocol.

    Args:
        socket_path: Path to Unix socket
        message_handler: Handler for incoming messages
        serializer: Message serializer (default: JSON)
        max_message_size: Maximum message size in bytes
        max_clients: Maximum concurrent clients

    Returns:
        Configured async server (not started)
    """
    server = AsyncUnixServer(socket_path=socket_path, max_clients=max_clients)
    protocol = LengthPrefixedProtocol(
        serializer=serializer or JSONSerializer(), max_message_size=max_message_size
    )
    return AsyncTransportServer(server, protocol, message_handler)


async def create_tcp_server(
    host: str = "localhost",
    port: int = 0,
    message_handler: MessageHandler | None = None,
    serializer: Serializer | None = None,
    max_message_size: int = 4 * 1024 * 1024,
    max_clients: int = 100,
) -> AsyncTransportServer:
    """
    Create async TCP server with length-prefixed protocol.

    Args:
        host: Server bind address
        port: Server port (0 for auto-assignment)
        message_handler: Handler for incoming messages
        serializer: Message serializer (default: JSON)
        max_message_size: Maximum message size in bytes
        max_clients: Maximum concurrent clients

    Returns:
        Configured async server (not started)
    """
    server = AsyncTCPServer(host=host, port=port, max_clients=max_clients)
    protocol = LengthPrefixedProtocol(
        serializer=serializer or JSONSerializer(), max_message_size=max_message_size
    )
    return AsyncTransportServer(server, protocol, message_handler)


# process_ipc migration helpers


class ProcessIPCCompatibleServer(AsyncTransportServer):
    """
    process_ipc compatible async server.

    Provides interface compatibility for migrating from process_ipc
    to universal_transport with minimal code changes.
    """

    def set_message_handler(self, handler: MessageHandler) -> None:
        """Set message handler (process_ipc compatible method)."""
        self.message_handler = handler

    async def send_to_client(self, client_id: str, message: Any) -> None:
        """Send message to specific client (process_ipc compatible method)."""
        session = self.get_client_by_id(client_id)
        if session:
            await session.send_message(message)
        else:
            raise ValueError(f"Client {client_id} not found")


async def create_process_ipc_server(
    socket_path: str, message_handler: MessageHandler | None = None
) -> ProcessIPCCompatibleServer:
    """
    Create process_ipc compatible server.

    Args:
        socket_path: Path to Unix socket
        message_handler: Handler for incoming messages

    Returns:
        process_ipc compatible server
    """
    server = AsyncUnixServer(socket_path=socket_path)
    protocol = LengthPrefixedProtocol(JSONSerializer())
    return ProcessIPCCompatibleServer(server, protocol, message_handler)


# New architecture factory functions


async def create_multi_client_unix_server(
    socket_path: str,
    message_handler: MessageHandler | None = None,
    serializer: Serializer | None = None,
    max_message_size: int = 4 * 1024 * 1024,
    max_clients: int = 100,
) -> MultiClientServer:
    """
    Create multi-client Unix socket server.

    Args:
        socket_path: Path to Unix socket
        message_handler: Handler for incoming messages
        serializer: Message serializer (default: JSON)
        max_message_size: Maximum message size in bytes
        max_clients: Maximum concurrent clients

    Returns:
        Configured multi-client server (not started)
    """
    server = AsyncUnixServer(socket_path=socket_path, max_clients=max_clients)
    protocol = LengthPrefixedProtocol(
        serializer=serializer or JSONSerializer(), max_message_size=max_message_size
    )
    return MultiClientServer(server, protocol, message_handler)


async def create_multi_client_tcp_server(
    host: str = "localhost",
    port: int = 0,
    message_handler: MessageHandler | None = None,
    serializer: Serializer | None = None,
    max_message_size: int = 4 * 1024 * 1024,
    max_clients: int = 100,
) -> MultiClientServer:
    """
    Create multi-client TCP server.

    Args:
        host: Server bind address
        port: Server port (0 for auto-assignment)
        message_handler: Handler for incoming messages
        serializer: Message serializer (default: JSON)
        max_message_size: Maximum message size in bytes
        max_clients: Maximum concurrent clients

    Returns:
        Configured multi-client server (not started)
    """
    server = AsyncTCPServer(host=host, port=port, max_clients=max_clients)
    protocol = LengthPrefixedProtocol(
        serializer=serializer or JSONSerializer(), max_message_size=max_message_size
    )
    return MultiClientServer(server, protocol, message_handler)


async def create_single_client_unix_server(
    socket_path: str,
    serializer: Serializer | None = None,
    max_message_size: int = 4 * 1024 * 1024,
) -> SingleClientServer:
    """
    Create single-client Unix socket server for Transport/MessagePump usage.

    Args:
        socket_path: Path to Unix socket
        serializer: Message serializer (default: JSON)
        max_message_size: Maximum message size in bytes

    Returns:
        Configured single-client server (not started)
    """
    server = AsyncUnixServer(
        socket_path=socket_path,
        max_clients=1,  # Enforce single client at transport level
    )
    protocol = LengthPrefixedProtocol(
        serializer=serializer or JSONSerializer(), max_message_size=max_message_size
    )
    return SingleClientServer(server, protocol)


async def create_single_client_tcp_server(
    host: str = "localhost",
    port: int = 0,
    serializer: Serializer | None = None,
    max_message_size: int = 4 * 1024 * 1024,
) -> SingleClientServer:
    """
    Create single-client TCP server for Transport/MessagePump usage.

    Args:
        host: Server bind address
        port: Server port (0 for auto-assignment)
        serializer: Message serializer (default: JSON)
        max_message_size: Maximum message size in bytes

    Returns:
        Configured single-client server (not started)
    """
    server = AsyncTCPServer(
        host=host,
        port=port,
        max_clients=1,  # Enforce single client at transport level
    )
    protocol = LengthPrefixedProtocol(
        serializer=serializer or JSONSerializer(), max_message_size=max_message_size
    )
    return SingleClientServer(server, protocol)
