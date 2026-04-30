"""Universal Protocol - HTTP/1.1 + WebSocket over Unix Sockets.

MVP Components:
- Error envelope with code, message, source, retryable, data
- Error codes and metadata for capacity/federation errors
- Stream and request ID generation
- WebSocket streaming (UnboundedStreamQueue, BoundedQueue, StreamContext, StreamClient)
- RPC client and server infrastructure
- Observability utilities

SSE primitives moved to `libs/sse/` (Phase 1 of sse-lib-promotion). Import
from `sse` directly. The `universal_protocol.sse` shim is deprecated.
"""

from universal_protocol.error_codes import (
    ERROR_METADATA,
    ErrorCode,
    get_http_status,
    is_retryable,
)
from universal_protocol.errors import (
    EngineError,
    ErrorSource,
    ProtocolError,
    RPCError,
    StreamError,
    error_envelope,
)
from universal_protocol.ids import generate_request_id, generate_stream_id
from universal_protocol.messages import (
    MessageEnvelope,
    ModelBusy,
    ModelIdle,
    ModelLoaded,
    ModelLoadFailed,
    ModelLoadingStarted,
    ModelUnloaded,
    ResourceUpdate,
    TelemetryHeartbeat,
    TelemetryPayload,
    TelemetrySource,
    parse_telemetry,
    validate_envelope_dict,
)
from universal_protocol.observability import get_debug_stats
from universal_protocol.rpc import AsyncRPCClient, RPCClient
from universal_protocol.server.asgi_app import app as asgi_app
from universal_protocol.ws import (
    BoundedQueue,
    QueueTimeoutError,
    StreamClient,
    StreamContext,
    StreamState,
    UnboundedStreamQueue,
)

__all__ = [
    # Error codes
    "ErrorCode",
    "ERROR_METADATA",
    "is_retryable",
    "get_http_status",
    # Errors
    "ProtocolError",
    "RPCError",
    "StreamError",
    "EngineError",
    "ErrorSource",
    "error_envelope",
    # IDs
    "generate_stream_id",
    "generate_request_id",
    # Messages
    "MessageEnvelope",
    "validate_envelope_dict",
    # Telemetry
    "TelemetryPayload",
    "TelemetrySource",
    "ResourceUpdate",
    "ModelLoaded",
    "ModelUnloaded",
    "ModelBusy",
    "ModelIdle",
    "ModelLoadingStarted",
    "ModelLoadFailed",
    "TelemetryHeartbeat",
    "parse_telemetry",
    # WebSocket Streaming
    "UnboundedStreamQueue",
    "BoundedQueue",
    "QueueTimeoutError",
    "StreamState",
    "StreamContext",
    "StreamClient",
    # RPC Clients
    "RPCClient",
    "AsyncRPCClient",
    # Observability
    "get_debug_stats",
    # ASGI Server
    "asgi_app",
]
