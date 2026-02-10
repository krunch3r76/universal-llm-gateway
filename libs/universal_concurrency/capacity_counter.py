"""
Capacity counter with release callback.

Pure counter primitive — no waiter management.
Used when wake logic is external (composed with FifoWaitQueue).

Invariant: active_count ≤ current_limit
Invariant: release() ⟹ on_release() called
Invariant: ¬await between check and mutation (async atomicity)
Invariant: over-release raises OverReleaseError (fail loudly)
"""

from __future__ import annotations

from collections.abc import Callable

from universal_logging import get_logger

from .exceptions import CapacityLimitError, OverReleaseError
from .types import CounterStats

logger = get_logger(__name__)


class CapacityCounter:
    """
    Capacity counter with release callback.

    Thread-safety: No locks needed — all mutations are synchronous
    (no await between check and action).

    Use case: Compose with FifoWaitQueue when custom wake logic needed.

    Args:
        limit: Static int (≥1) or callable returning current limit (≥1).
        on_release: Callback invoked on each release() (for waking waiters).
        counter_id: Optional identifier for logging/metrics.
    """

    def __init__(
        self,
        limit: int | Callable[[], int],
        *,
        on_release: Callable[[], None] | None = None,
        counter_id: str | None = None,
    ) -> None:
        if isinstance(limit, int) and limit < 1:
            raise CapacityLimitError(f"limit must be >= 1, got {limit}")

        self._limit_source = limit
        self._on_release = on_release
        self._counter_id = counter_id
        self._active_count = 0
        self._total_acquired = 0
        self._total_released = 0

    @property
    def current_limit(self) -> int:
        """
        Current limit (evaluates callable if dynamic).

        Raises:
            CapacityLimitError: If callable returns < 1.
        """
        if callable(self._limit_source):
            limit = self._limit_source()
            if limit < 1:
                raise CapacityLimitError(
                    f"Dynamic limit callable returned {limit}, must be >= 1"
                )
            return limit
        return self._limit_source

    @property
    def active_count(self) -> int:
        """Number of currently held slots."""
        return self._active_count

    def try_acquire(self, request_id: str) -> bool:
        """
        Try to acquire capacity slot (atomic).

        No await = no interleaving = safe without lock.

        Returns:
            True if slot acquired, False if at capacity.
        """
        try:
            limit = self.current_limit
        except CapacityLimitError as e:
            logger.error(
                "[COUNTER:%s] Dynamic limit invalid, cannot acquire: %s",
                self._counter_id,
                e,
            )
            return False

        if self._active_count < limit:
            self._active_count += 1
            self._total_acquired += 1

            if self._counter_id is not None:
                logger.debug(
                    "🟢 [COUNTER:%s] %s acquired (active=%d/%d)",
                    self._counter_id,
                    request_id[:8],
                    self._active_count,
                    limit,
                )
            return True
        return False

    def release(self) -> None:
        """
        Release capacity slot and invoke callback.

        Synchronous — safe to call from any context.
        Callback is invoked AFTER decrementing (for wake logic).

        Raises:
            OverReleaseError: If called when active_count is 0.
        """
        if self._active_count == 0:
            raise OverReleaseError(
                f"[COUNTER:{self._counter_id}] release() called but active_count=0. "
                f"This indicates a bug: release() called without matching acquire()."
            )

        self._active_count -= 1
        self._total_released += 1

        if self._counter_id is not None:
            try:
                limit = self.current_limit
            except CapacityLimitError:
                limit = -1  # Telemetry failure, don't break release
            logger.debug(
                "🔓 [COUNTER:%s] Released (active=%d/%d)",
                self._counter_id,
                self._active_count,
                limit,
            )

        # Invoke callback AFTER state change (wake next waiter)
        if self._on_release is not None:
            self._on_release()

    @property
    def stats(self) -> CounterStats:
        """Counter statistics."""
        try:
            limit = self.current_limit
        except CapacityLimitError as e:
            logger.error(
                "[COUNTER:%s] Dynamic limit invalid in stats: %s",
                self._counter_id,
                e,
            )
            limit = -1

        return CounterStats(
            active=self._active_count,
            limit=limit,
            total_acquired=self._total_acquired,
            total_released=self._total_released,
        )
