"""Stream state types for validation.

Sum types for stream lookup results, eliminating tuple-length detection.
"""

import asyncio
from dataclasses import dataclass

from universal_protocol.ws.lifecycle import StreamContext
from universal_protocol.ws.queue_protocol import StreamQueueProtocol


@dataclass(slots=True, frozen=True)
class StreamStateOk:
    """Successful stream state lookup.

    Fields:
        context: StreamContext for lifecycle management
        queue: Queue for frame streaming
        cancellation_event: Event to signal stream cancellation
    """

    context: StreamContext
    queue: StreamQueueProtocol
    cancellation_event: asyncio.Event


@dataclass(slots=True, frozen=True)
class StreamStateErr:
    """Failed stream state lookup.

    Fields:
        code: Error code (e.g., "INVALID_STREAM_ID")
        message: Human-readable error message
    """

    code: str
    message: str
