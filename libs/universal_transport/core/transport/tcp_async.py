"""
Async TCP socket transport implementation.

This module provides async TCP socket transport that eliminates
asyncio readline buffer limits by using asyncio.readexactly() for
length-prefixed protocols.

Key features:
- Uses asyncio.readexactly() (no readline buffer limits)
- Multi-client server support
- IPv4 and IPv6 support
- Configurable connection timeout and keepalive
- Compatible with length-prefixed protocol
- Network communication between different machines

This module is a backward-compatibility facade that imports from
the modularized tcp/ subdirectory.
"""

# Import from modularized implementation for backward compatibility
from .tcp import (
    AsyncTCPClientHandler,
    AsyncTCPConnectionError,
    AsyncTCPConnectionState,
    AsyncTCPReceiveError,
    AsyncTCPSendError,
    AsyncTCPServer,
    AsyncTCPTransport,
    AsyncTCPTransportError,
)

# Re-export for backward compatibility
__all__ = [
    "AsyncTCPConnectionState",
    "AsyncTCPTransportError",
    "AsyncTCPConnectionError",
    "AsyncTCPSendError",
    "AsyncTCPReceiveError",
    "AsyncTCPTransport",
    "AsyncTCPServer",
    "AsyncTCPClientHandler",
]
