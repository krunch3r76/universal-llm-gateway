"""
Message pump module.

Provides concurrent I/O, correlation matching, and message queuing for transport-based communication.
"""

from .interfaces import MessagePumpInterface, MessageReader, MessageWriter
from .pump import MessagePump, default_get_correlation_id

__all__ = [
    "MessagePump",
    "MessageReader",
    "MessageWriter",
    "MessagePumpInterface",
    "default_get_correlation_id",
]
