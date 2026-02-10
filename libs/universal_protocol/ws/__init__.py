"""WebSocket streaming components for Universal Protocol.

Implements one-shot streaming model with immediate cleanup on any failure.
No reconnect logic, no heartbeat, no half-closed states.
"""

from .bounded_queue import BoundedQueue, QueueTimeoutError
from .client import StreamClient
from .endpoint import (
    STREAM_IDLE_TIMEOUT_SECONDS,
    fail_stream,
    stream_handler,
)
from .frame_types import (
    CODE_CANCELLED,
    CODE_IDLE_TIMEOUT,
    CODE_MODEL_UNLOADED,
    CODE_QUEUE_CLOSED,
    CODE_STREAM_ERROR,
    FRAME_DONE,
    FRAME_ERR,
    FRAME_TOKEN,
    TERMINAL_FRAME_TYPES,
    get_close_code,
    is_terminal_frame,
    make_control_frame,
)
from .lifecycle import StreamContext, StreamState
from .producer import producer_put
from .queue_protocol import StreamQueueProtocol
from .registry import EntryKind, StreamEntry, StreamRegistry, stream_registry
from .stream_queue import UnboundedStreamQueue

__all__ = [
    # Queue types
    "UnboundedStreamQueue",
    "BoundedQueue",
    "QueueTimeoutError",
    "StreamQueueProtocol",
    # Lifecycle
    "StreamState",
    "StreamContext",
    # Registry
    "StreamRegistry",
    "StreamEntry",
    "EntryKind",
    "stream_registry",
    # Endpoint handlers
    "stream_handler",
    "fail_stream",
    "STREAM_IDLE_TIMEOUT_SECONDS",
    # Producer
    "producer_put",
    # Client
    "StreamClient",
    # Frame types
    "CODE_CANCELLED",
    "CODE_IDLE_TIMEOUT",
    "CODE_MODEL_UNLOADED",
    "CODE_QUEUE_CLOSED",
    "CODE_STREAM_ERROR",
    "FRAME_DONE",
    "FRAME_ERR",
    "FRAME_TOKEN",
    "TERMINAL_FRAME_TYPES",
    "get_close_code",
    "is_terminal_frame",
    "make_control_frame",
]
