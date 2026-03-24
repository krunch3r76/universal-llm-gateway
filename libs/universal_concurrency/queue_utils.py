"""
Asyncio queue utilities with correct Python 3.12+ exception handling.

Python 3.12 broke the `asyncio.QueueEmpty` → `queue.Empty` inheritance chain.
In 3.12, `asyncio.QueueEmpty` inherits directly from `Exception`, not `queue.Empty`.
Code catching `queue.Empty` after `asyncio.Queue.get_nowait()` silently passes
the exception through, crashing the caller's task with no log entry.

Use `drain_queue_batch()` for non-blocking batch drains, or import `QUEUE_EMPTY_ERRORS`
for bare `except` clauses when you need to handle the empty case yourself.

Invariant: ∀ asyncio.Queue.get_nowait() callers: catch QUEUE_EMPTY_ERRORS, ¬queue.Empty alone.
"""

from __future__ import annotations

import asyncio
import queue

# Python ≤3.11: asyncio.QueueEmpty was aliased to queue.Empty (same object).
# Python 3.12+:  asyncio.QueueEmpty is a distinct class inheriting from Exception only.
# Catching queue.Empty alone silently misses QueueEmpty on 3.12 → task crashes.
QUEUE_EMPTY_ERRORS: tuple[type[BaseException], ...] = (queue.Empty, asyncio.QueueEmpty)


def drain_queue_batch[T](q: asyncio.Queue[T], max_size: int) -> list[T]:
    """Drain up to max_size items from an asyncio.Queue without blocking.

    Stops early if the queue is empty. Catches both asyncio.QueueEmpty (3.12+)
    and queue.Empty (pre-3.12 alias) so callers are insulated from the version
    difference.

    Prefer this over a manual `while get_nowait()` loop. That loop requires
    catching QUEUE_EMPTY_ERRORS, which is easy to get wrong.

    Args:
        q:        The asyncio.Queue to drain.
        max_size: Maximum items to pull in one call.

    Returns:
        List of drained items (may be empty; never longer than max_size).
    """
    batch: list[T] = []
    while len(batch) < max_size:
        try:
            batch.append(q.get_nowait())
        except QUEUE_EMPTY_ERRORS:
            break
    return batch
