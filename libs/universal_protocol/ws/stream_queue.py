"""Unbounded streaming queue for WebSocket streaming.

Simple unbounded queue designed for non-scaling single-user systems
where simplicity and responsiveness matter more than resource protection.

Features:
- Unbounded capacity for maximum responsiveness
- No producer timeouts - never blocks
- No size limits - trusts content generators
- Simple error handling
"""

import asyncio
import time
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


class UnboundedStreamQueue:
    """Simple unbounded queue for streaming frames.

    Provides maximum responsiveness by removing all limits,
    timeouts, and backpressure mechanisms.

    Design:
    - Unbounded capacity for maximum responsiveness
    - No producer timeouts - never blocks
    - No size limits - trusts content generators
    - Simple error handling
    """

    def __init__(self, stream_id: str = None) -> None:
        """Initialize unbounded streaming queue.

        Args:
            stream_id: Optional stream identifier for logging
        """
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._closed = False
        self._stream_id = stream_id

    def _ensure_queue(self) -> asyncio.Queue[dict[str, Any]]:
        """Ensure queue is initialized (lazy initialization).

        Returns:
            The initialized queue
        """
        if self._queue is None:
            self._queue = asyncio.Queue()
        return self._queue

    async def put(
        self, frame: dict[str, Any], timeout_seconds: float | None = None
    ) -> None:
        """Enqueue a frame without any limits or timeouts.

        Args:
            frame: Frame dict to enqueue
            timeout_seconds: Ignored (kept for API compatibility)

        Raises:
            RuntimeError: If queue is closed
        """
        if self._closed:
            raise RuntimeError("Queue is closed")

        queue = self._ensure_queue()

        # Streaming stall debugging: Log frame timing for investigation
        timestamp = time.perf_counter()
        logger.debug(
            f"STREAM_DEBUG: UnboundedStreamQueue.put - "
            f"stream_id={self._stream_id}, "
            f"queue_size={queue.qsize()}, "
            f"timestamp={timestamp:.6f}"
        )

        # Simple put - no timeouts, no limits
        await queue.put(frame)

    async def get(self) -> dict[str, Any]:
        """Dequeue a frame.

        Returns:
            Frame dict from queue

        Raises:
            RuntimeError: If queue is closed
        """
        if self._closed:
            raise RuntimeError("Queue is closed")

        queue = self._ensure_queue()
        return await queue.get()

    def put_nowait(self, frame: dict[str, Any]) -> None:
        """Enqueue a frame without blocking.

        For unbounded queue, this always succeeds unless closed.

        Raises:
            RuntimeError: If queue is closed
        """
        if self._closed:
            raise RuntimeError("Queue is closed")

        queue = self._ensure_queue()
        queue.put_nowait(frame)

    def qsize(self) -> int:
        """Return current queue size.

        Returns:
            Number of frames currently in queue
        """
        if self._queue is None:
            return 0
        return self._queue.qsize()

    async def close(self) -> None:
        """Close the queue and release resources.

        Postcondition: ∃ terminal_frame ∈ queue ⟹ pending get() will return

        Emits a QUEUE_CLOSED terminal frame before marking closed,
        ensuring any blocked consumer will unblock and exit.
        """
        if self._closed:
            return  # Idempotent

        # Emit terminal frame BEFORE marking closed (consumer must see it)
        from universal_protocol.ws.frame_types import CODE_QUEUE_CLOSED, FRAME_ERR

        terminal_frame = {
            "t": FRAME_ERR,
            "code": CODE_QUEUE_CLOSED,
            "message": "Queue closed",
            "source": "queue",
        }

        queue = self._ensure_queue()
        try:
            queue.put_nowait(terminal_frame)
        except Exception:
            pass  # Best effort - queue may already be full/closed

        self._closed = True

    def __repr__(self) -> str:
        """String representation."""
        qsize = self._queue.qsize() if self._queue is not None else 0
        return f"UnboundedStreamQueue(size={qsize}, closed={self._closed})"
