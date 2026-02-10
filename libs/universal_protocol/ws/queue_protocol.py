"""Queue protocol for stream registry.

Defines minimal interface for stream queues, allowing both
BoundedQueue and UnboundedStreamQueue to be used interchangeably.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StreamQueueProtocol(Protocol):
    """Protocol for stream queues.

    Invariant:
        ∀ queue ∈ {BoundedQueue, UnboundedStreamQueue}: queue ⊆ StreamQueueProtocol

    Implementations:
        - UnboundedStreamQueue: Current default, no backpressure
        - BoundedQueue: Future option, with timeout/limits (not currently used)

    Methods:
        put: Enqueue a frame (async, may block on backpressure)
        put_nowait: Enqueue a frame (sync, raises if full)
        get: Dequeue a frame (async)
        close: Close the queue (async)
        qsize: Current queue size (sync)
    """

    async def put(
        self,
        frame: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> None:
        """Enqueue a frame (may block on backpressure).

        Inputs:
            frame: Frame dict to enqueue
            timeout_seconds: Optional timeout for bounded queues (ignored by unbounded)

        Raises:
            QueueTimeoutError: If timeout exceeded (BoundedQueue only)
            ValueError: If frame exceeds size limits (BoundedQueue only)
            RuntimeError: If queue is closed
        """
        ...

    def put_nowait(self, frame: dict[str, Any]) -> None:
        """Enqueue a frame without blocking.

        For control events only. Does NOT enforce size limits since
        control frames are small and critical.

        Raises:
            asyncio.QueueFull: If queue is at capacity (bounded queue)
            RuntimeError: If queue is closed
        """
        ...

    async def get(self) -> dict[str, Any]:
        """Dequeue a frame.

        Outputs:
            Frame dict from queue

        Raises:
            RuntimeError: If queue is closed
        """
        ...

    async def close(self) -> None:
        """Close the queue and release resources.

        Postcondition: ∃ terminal_frame ∈ queue (best effort)
        """
        ...

    def qsize(self) -> int:
        """Return current queue size."""
        ...
