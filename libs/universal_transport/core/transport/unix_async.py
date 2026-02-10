"""
Async Unix domain socket transport implementation.

This module provides async Unix domain socket transport that eliminates
asyncio readline buffer limits by using asyncio.readexactly() for
length-prefixed protocols.

Key features:
- Uses asyncio.readexactly() (no readline buffer limits)
- Multi-client server support
- Proper connection handling and cleanup
- Compatible with length-prefixed protocol
- High-performance local IPC

This module is a backward-compatibility facade that imports from
the modularized unix/ subdirectory.
"""

# Import from modularized implementation for backward compatibility
from .unix import (
    AsyncUnixClientHandler,
    AsyncUnixConnectionError,
    AsyncUnixConnectionState,
    AsyncUnixReceiveError,
    AsyncUnixSendError,
    AsyncUnixServer,
    AsyncUnixTransport,
    AsyncUnixTransportError,
)

# Re-export for backward compatibility
__all__ = [
    "AsyncUnixConnectionState",
    "AsyncUnixTransportError",
    "AsyncUnixConnectionError",
    "AsyncUnixSendError",
    "AsyncUnixReceiveError",
    "AsyncUnixTransport",
    "AsyncUnixServer",
    "AsyncUnixClientHandler",
]
