"""Pipeline admission control — bounds concurrent pipeline executions.

Uses an asyncio.Queue as a token pool to limit how many pipelines execute
concurrently.  Acquiring a token blocks (with timeout) until one is available;
releasing returns it to the pool.  No locks — the queue itself serializes
admission.

Config surface (stargate.yaml):
    pipeline.max_concurrent_executions  — token pool size  (default 2)
    pipeline.admission_timeout_seconds  — max wait         (default 120)
"""

from __future__ import annotations

import asyncio
import time


class PipelineAdmissionQueue:
    """Bounds concurrent pipeline executions to prevent GPU contention.

    Token-pool pattern: pre-fill a maxsize queue with tokens.  acquire() pops
    a token (blocking up to ``timeout``); release() pushes one back.
    """

    def __init__(self, max_concurrent: int = 2) -> None:
        self._tokens: asyncio.Queue[bool] = asyncio.Queue(maxsize=max_concurrent)
        for _ in range(max_concurrent):
            self._tokens.put_nowait(True)
        self._waiting: int = 0

    async def acquire(self, timeout: float = 120.0) -> float:
        """Wait for an admission token.

        Returns the wall-clock wait time in milliseconds.
        Raises ``asyncio.TimeoutError`` if no token becomes available
        within *timeout* seconds.
        """
        t0 = time.monotonic()
        self._waiting += 1
        try:
            await asyncio.wait_for(self._tokens.get(), timeout=timeout)
        finally:
            self._waiting -= 1
        return (time.monotonic() - t0) * 1000.0

    def release(self) -> None:
        """Return an admission token after pipeline completes or is cancelled."""
        self._tokens.put_nowait(True)

    @property
    def active(self) -> int:
        """Number of tokens currently held (pipelines executing)."""
        return self._tokens.maxsize - self._tokens.qsize()

    @property
    def waiting(self) -> int:
        """Number of callers blocked in acquire()."""
        return self._waiting

    @property
    def max_concurrent(self) -> int:
        return self._tokens.maxsize
