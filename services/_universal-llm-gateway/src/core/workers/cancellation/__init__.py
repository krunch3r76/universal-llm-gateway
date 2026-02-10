"""
Stream cancellation operations.

Provides event-driven cancellation emission with fallback state mutation.
"""

from .emission import (
    build_stream_cancelled_event,
    emit_stream_cancelled_nowait,
    emit_stream_cancelled_or_force_idle,
    fallback_force_idle_on_event_failure,
)

__all__ = [
    "build_stream_cancelled_event",
    "emit_stream_cancelled_nowait",
    "emit_stream_cancelled_or_force_idle",
    "fallback_force_idle_on_event_failure",
]
