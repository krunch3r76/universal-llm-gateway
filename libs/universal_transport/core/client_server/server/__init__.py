"""Async server implementation modules."""

from .base_server import (
    AsyncServerStartError,
    AsyncServerStopError,
    AsyncTransportServerError,
    BaseAsyncServer,
)
from .factories import (
    ProcessIPCCompatibleServer,
    create_multi_client_tcp_server,
    create_multi_client_unix_server,
    create_process_ipc_server,
    create_single_client_tcp_server,
    create_single_client_unix_server,
    create_tcp_server,
    create_unix_server,
)
from .multi_client_server import MultiClientServer
from .server_impl import AsyncTransportServer, ServerSessionTransport
from .session import AsyncClientSession
from .single_client_server import SingleClientServer

__all__ = [
    # Sessions
    "AsyncClientSession",
    # Legacy server (will be deprecated)
    "AsyncTransportServer",
    "ServerSessionTransport",
    # New server architecture
    "BaseAsyncServer",
    "MultiClientServer",
    "SingleClientServer",
    # Exceptions
    "AsyncTransportServerError",
    "AsyncServerStartError",
    "AsyncServerStopError",
    # Factory functions
    "create_unix_server",
    "create_tcp_server",
    "ProcessIPCCompatibleServer",
    "create_process_ipc_server",
    # New factory functions
    "create_multi_client_unix_server",
    "create_multi_client_tcp_server",
    "create_single_client_unix_server",
    "create_single_client_tcp_server",
]
