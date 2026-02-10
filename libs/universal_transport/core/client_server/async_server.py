"""
Async server wrapper for universal_transport.

This module provides a high-level async server interface that combines
transport and protocol layers for easy message-based multi-client handling.

Key features:
- Multi-client message-based server
- Works with length-prefixed protocol (no readline buffer limits)
- Support for Unix and TCP transports
- Pluggable message handlers
- Automatic client management
- Compatible with process_ipc migration patterns

This module is a backward-compatibility facade that imports from
the modularized server/ subdirectory.
"""

# Import from modularized implementation for backward compatibility
from .server import (
    AsyncClientSession,
    AsyncTransportServer,
    ProcessIPCCompatibleServer,
    create_process_ipc_server,
    create_tcp_server,
    create_unix_server,
)
from .server.server_impl import (
    AsyncServerStartError,
    AsyncServerStopError,
    AsyncTransportServerError,
    MessageHandler,
)

# Re-export for backward compatibility
__all__ = [
    "AsyncTransportServerError",
    "AsyncServerStartError",
    "AsyncServerStopError",
    "MessageHandler",
    "AsyncClientSession",
    "AsyncTransportServer",
    "create_unix_server",
    "create_tcp_server",
    "ProcessIPCCompatibleServer",
    "create_process_ipc_server",
]
