"""
FIFO waiter queue for event-driven wake patterns.

Used when capacity decisions are external (events trigger wake).
Does NOT track active count — caller decides when to call wake_next().

Invariant: wake_next() wakes oldest non-done waiter
Invariant: ¬await between check and mutation (async atomicity)
Invariant: O(1) enqueue and wake operations
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from time import monotonic

from universal_logging import get_logger

from .types import WaitQueueStats

logger = get_logger(__name__)


@dataclass(slots=True, kw_only=True)
class _Waiter:
    """Internal waiter entry."""

    request_id: str
    future: asyncio.Future[None]
    enqueue_time: float


class FifoWaitQueue:
    """
    FIFO waiter queue with cancellation support.

    Thread-safety: No locks needed — all mutations are synchronous
    (no await between check and action).

    Performance: O(1) enqueue and wake via deque.
    """

    def __init__(self, queue_id: str | None = None) -> None:
        """
        Args:
            queue_id: Optional identifier for logging/metrics.
                      If None, debug logging is suppressed.
        """
        self._queue_id = queue_id
        self._waiters: deque[_Waiter] = deque()
        self._woken_total = 0
        self._cancelled_total = 0

    def enqueue(self, request_id: str) -> asyncio.Future[None]:
        """
        Add waiter to queue, return Future to await.

        The caller awaits the returned Future (with optional timeout).
        Future resolves to None when wake_next() selects this waiter.
        Future raises CancelledError if cancel() is called for this request_id.

        Returns:
            Future that resolves to None when woken, or raises on cancellation.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        waiter = _Waiter(
            request_id=request_id,
            future=future,
            enqueue_time=monotonic(),
        )
        self._waiters.append(waiter)

        if self._queue_id is not None:
            logger.debug(
                "🔶 [WAIT_QUEUE:%s] %s enqueued (position=%d)",
                self._queue_id,
                request_id[:8],
                len(self._waiters),
            )

        return future

    def wake_next(self) -> str | None:
        """
        Wake oldest non-done waiter (FIFO).

        Skips done/cancelled waiters automatically (they timed out or
        were cancelled externally).

        Returns:
            request_id of woken waiter, or None if no eligible waiter.
        """
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.future.done():
                waiter.future.set_result(None)
                self._woken_total += 1

                wait_time = monotonic() - waiter.enqueue_time
                if self._queue_id is not None:
                    logger.debug(
                        "🟢 [WAIT_QUEUE:%s] Woke %s after %.1fs (remaining=%d)",
                        self._queue_id,
                        waiter.request_id[:8],
                        wait_time,
                        len(self._waiters),
                    )
                return waiter.request_id
            # Skip done waiters (timeout/cancelled elsewhere)

        return None

    def cancel(self, request_id: str) -> bool:
        """
        Cancel waiter by request_id.

        Removes waiter from queue and sets CancelledError on its Future
        (if not already done). The awaiter will receive the exception.

        Returns:
            True if found and removed, False if not found.
        """
        for i, waiter in enumerate(self._waiters):
            if waiter.request_id == request_id:
                # Remove from deque (O(n) but cancel is rare)
                del self._waiters[i]

                if not waiter.future.done():
                    waiter.future.set_exception(
                        asyncio.CancelledError(f"Request {request_id} cancelled")
                    )
                self._cancelled_total += 1

                if self._queue_id is not None:
                    logger.info(
                        "🛑 [WAIT_QUEUE:%s] Cancelled %s",
                        self._queue_id,
                        request_id[:8],
                    )
                return True
        return False

    def peek_oldest_wait_time(self) -> float | None:
        """
        Get wait time of oldest waiter (for monitoring).

        Returns:
            Seconds the oldest waiter has been waiting, or None if empty.
        """
        for waiter in self._waiters:
            if not waiter.future.done():
                return monotonic() - waiter.enqueue_time
        return None

    @property
    def queue_length(self) -> int:
        """Number of waiters (including done pending cleanup)."""
        return len(self._waiters)

    @property
    def active_waiter_count(self) -> int:
        """Number of non-done waiters (excludes timed-out/cancelled)."""
        return sum(1 for w in self._waiters if not w.future.done())

    @property
    def stats(self) -> WaitQueueStats:
        """Queue statistics."""
        return WaitQueueStats(
            queued=len(self._waiters),
            woken_total=self._woken_total,
            cancelled_total=self._cancelled_total,
        )
