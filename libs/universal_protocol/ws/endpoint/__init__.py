"""WebSocket endpoint handlers - modular, SRP-compliant.

Replaces monolithic stream_handler from ws/handlers.py.
"""

from .cleanup import cleanup_websocket_stream
from .config import STREAM_IDLE_TIMEOUT_SECONDS
from .state import StreamStateErr, StreamStateOk
from .stream_handler import stream_handler
from .stream_loop import read_and_send_frames
from .validation import fail_stream, get_stream_state, validate_stream_entry

__all__ = [
    "stream_handler",
    "fail_stream",
    "get_stream_state",
    "validate_stream_entry",
    "read_and_send_frames",
    "cleanup_websocket_stream",
    "STREAM_IDLE_TIMEOUT_SECONDS",
    "StreamStateOk",
    "StreamStateErr",
]
