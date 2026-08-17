"""
Universal Transport - Ecosystem Foundation Layer

Modern async transport layer with length-prefixed framing that eliminates
asyncio readline buffer limits and handles multi-MB messages efficiently.

Key features:
- Length-prefixed protocol (eliminates readline 64KB buffer limits)
- Async transports using asyncio.readexactly() (no readline issues)
- Pluggable serialization (JSON, MessagePack, Protobuf, Raw Binary)
- Multi-MB payload support without buffer scanning
- Clean, modern async API
"""

# Version
__version__ = "1.0.0"  # Major version - legacy code removed

# === Async Transport Layer ===

# Async transports (no readline buffer limits)
# Async client/server (high-level interface)
from .core.client_server.async_client import (
    AsyncTransportClient,
    ProcessIPCCompatibleClient,
    create_process_ipc_client,
    create_tcp_client,
    create_unix_client,
)
from .core.client_server.async_server import (
    AsyncClientSession,
    AsyncTransportServer,
    ProcessIPCCompatibleServer,
    create_process_ipc_server,
    create_tcp_server,
    create_unix_server,
)
from .core.client_server.server.server_impl import ServerSessionTransport
from .core.exceptions import TransportError, UTConnectionError

# Core interfaces and exceptions
from .core.interfaces import IPCTransport, Transport

# Message pump (concurrent I/O with correlation matching)
from .core.message_pump import (
    MessagePump,
    MessagePumpInterface,
    MessageReader,
    MessageWriter,
    default_get_correlation_id,
)

# Length-prefixed protocol (primary framing, eliminates readline issues)
from .core.protocol.length_prefixed import (
    LengthPrefixedProtocol,
    create_json_protocol,
    create_messagepack_protocol,
    create_protobuf_protocol,
    create_raw_protocol,
)

# Serialization layer (pluggable formats)
from .core.protocol.serializers import (
    # Concrete serializers
    JSONSerializer,
    MessagePackSerializer,
    ProtobufSerializer,
    RawBinarySerializer,
    # Abstract base
    Serializer,
    # Utilities
    get_serializer_by_name,
    list_available_serializers,
)
from .core.transport.tcp_async import AsyncTCPServer, AsyncTCPTransport
from .core.transport.unix_async import AsyncUnixServer, AsyncUnixTransport

# === Specialized Patterns ===
# Monitoring patterns (async, no buffer limits)
from .specialized import (
    AsyncMonitoringClient,
    AsyncMonitoringServer,
    MonitoringConfig,
    MonitoringEvent,
)

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('stargate',)

# === Public API ===

__all__ = [
    # Version
    "__version__",
    # === Core Interfaces ===
    # Transport interface (message-level)
    "Transport",
    "IPCTransport",
    # Exceptions
    "TransportError",
    "UTConnectionError",
    # Message pump
    "MessagePump",
    "MessageReader",
    "MessageWriter",
    "MessagePumpInterface",
    "default_get_correlation_id",
    # === Async Transports ===
    # Low-level transports
    "AsyncUnixTransport",
    "AsyncUnixServer",
    "AsyncTCPTransport",
    "AsyncTCPServer",
    # Protocol layer
    "LengthPrefixedProtocol",
    "create_json_protocol",
    "create_messagepack_protocol",
    "create_raw_protocol",
    "create_protobuf_protocol",
    # Serializers
    "Serializer",
    "JSONSerializer",
    "MessagePackSerializer",
    "RawBinarySerializer",
    "ProtobufSerializer",
    "get_serializer_by_name",
    "list_available_serializers",
    # High-level client/server
    "AsyncTransportClient",
    "AsyncTransportServer",
    "AsyncClientSession",
    "create_unix_client",
    "create_tcp_client",
    "create_unix_server",
    "create_tcp_server",
    # Server session transport adapter
    "ServerSessionTransport",
    # process_ipc migration helpers
    "ProcessIPCCompatibleClient",
    "create_process_ipc_client",
    "ProcessIPCCompatibleServer",
    "create_process_ipc_server",
    # === Specialized Patterns ===
    # Monitoring (async, no buffer limits)
    "AsyncMonitoringServer",
    "AsyncMonitoringClient",
    "MonitoringEvent",
    "MonitoringConfig",
]
