"""
Message pump module.

Provides concurrent I/O, correlation matching, and message queuing for
transport-based communication.
"""

from .interfaces import MessagePumpInterface, MessageReader, MessageWriter
from .message_identification import default_get_correlation_id
from .pump import MessagePump

__all__ = [
    "MessagePump",
    "MessageReader",
    "MessageWriter",
    "MessagePumpInterface",
    "default_get_correlation_id",
]
