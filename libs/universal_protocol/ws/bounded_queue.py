"""
Bounded async queue with configurable capacity and backpressure.

General-purpose queue for any bounded async producer-consumer pattern:
- WebSocket streaming
- Federation message passing
- Event forwarding

BREAKING CHANGE: Removes SSE-specific logic from previous (unused) implementation.
Existing exports preserved for API compatibility, implementation completely changed.
"""

import asyncio
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


class QueueTimeoutError(Exception):
    """Raised when producer times out waiting for queue capacity."""

    pass


class BoundedQueue:
    """
    Bounded async queue with optional timeout and fire-and-forget support.

    Implements StreamQueueProtocol.

    Design:
    - Configurable capacity (default: 128)
    - put(): async with optional timeout
    - put_nowait(): sync, raises QueueFull
    - try_put(): sync, returns bool (fire-and-forget)
    - get(): async consumer
    - Tracks overflow count for monitoring

    INVARIANT:
      ∀ put operation: capacity_exceeded ⟹ (block | raise | return False)
      ∀ queue: overflow_count tracks rejected puts
    """

    def __init__(
        self,
        max_size: int = 128,
        *,
        queue_id: str | None = None,
    ) -> None:
        """
        Initialize bounded queue.

        Args:
            max_size: Maximum queue capacity (0 = unbounded)
            queue_id: Optional identifier for logging/metrics
        """
        self._max_size = max_size
        self._queue_id = queue_id
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_size)
        self._closed = False
        self._overflow_count = 0

    @property
    def overflow_count(self) -> int:
        """Number of failed put attempts due to capacity."""
        return self._overflow_count

    @property
    def max_size(self) -> int:
        """Maximum queue capacity."""
        return self._max_size

    def qsize(self) -> int:
        """Current queue depth."""
        return self._queue.qsize()

    def is_full(self) -> bool:
        """Check if queue is at capacity."""
        return self._queue.full()

    def is_empty(self) -> bool:
        """Check if queue is empty."""
        return self._queue.empty()

    def is_closed(self) -> bool:
        """Check if queue is closed."""
        return self._closed

    async def put(
        self,
        item: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> None:
        """
        Enqueue item, optionally waiting for capacity.

        Args:
            item: Dict to enqueue
            timeout_seconds: Max wait time (None = wait forever)

        Raises:
            QueueTimeoutError: If timeout exceeded
            RuntimeError: If queue is closed
        """
        if self._closed:
            raise RuntimeError("Queue is closed")

        try:
            if timeout_seconds is not None:
                await asyncio.wait_for(
                    self._queue.put(item),
                    timeout=timeout_seconds,
                )
            else:
                await self._queue.put(item)
        except TimeoutError as e:
            self._overflow_count += 1
            raise QueueTimeoutError(f"Queue full after {timeout_seconds}s") from e

    def put_nowait(self, item: dict[str, Any]) -> None:
        """
        Enqueue item without waiting (raises if full).

        Raises:
            asyncio.QueueFull: If at capacity
            RuntimeError: If queue is closed
        """
        if self._closed:
            raise RuntimeError("Queue is closed")

        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self._overflow_count += 1
            raise

    def try_put(self, item: dict[str, Any]) -> bool:
        """
        Try to enqueue item (fire-and-forget pattern).

        Returns:
            True if enqueued, False if full or closed
        """
        if self._closed:
            return False

        try:
            self._queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            self._overflow_count += 1
            return False

    async def get(self) -> dict[str, Any]:
        """
        Dequeue next item (waits if empty).

        Raises:
            RuntimeError: If queue is closed and empty
        """
        if self._closed and self._queue.empty():
            raise RuntimeError("Queue is closed")

        return await self._queue.get()

    def get_nowait(self) -> dict[str, Any]:
        """
        Dequeue item without waiting.

        Raises:
            asyncio.QueueEmpty: If queue is empty
            RuntimeError: If queue is closed and empty
        """
        if self._closed and self._queue.empty():
            raise RuntimeError("Queue is closed")

        return self._queue.get_nowait()

    async def close(self) -> None:
        """
        Close queue. Subsequent puts will fail.

        Does NOT drain remaining items - consumer should handle.
        """
        self._closed = True

    def __repr__(self) -> str:
        return (
            f"BoundedQueue(size={self.qsize()}, "
            f"max={self._max_size}, "
            f"closed={self._closed}, "
            f"overflows={self._overflow_count})"
        )
