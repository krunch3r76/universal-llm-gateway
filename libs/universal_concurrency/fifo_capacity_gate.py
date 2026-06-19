"""
FIFO capacity gate (semaphore replacement).

Provides bounded concurrency with guaranteed FIFO fairness.
Supports static or dynamic limits.

Invariant: ∀ t: active_count ≤ current_limit
Invariant: release() transfers slot FIFO or decrements active
Invariant: ¬await between check and mutation (async atomicity)
Invariant: over-release raises RuntimeError (fail loudly)
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

from universal_logging import get_logger

from .exceptions import CapacityLimitError, OverReleaseError
from .types import GateStats

logger = get_logger(__name__)


@dataclass(slots=True, kw_only=True)
class _GateWaiter:
    """Internal waiter entry for capacity gate."""

    request_id: str
    future: asyncio.Future[None]
    enqueue_time: float


class FifoCapacityGate:
    """
    FIFO-fair capacity gate.

    Replaces asyncio.Semaphore with guaranteed FIFO ordering.

    Thread-safety: Uses asyncio.Lock only around multi-step operations
    that include awaits. Atomic sync operations don't need locking.

    Failure mode: Raises on invariant violations (no silent failures).
    """

    def __init__(
        self,
        limit: int | Callable[[], int],
        *,
        gate_id: str | None = None,
    ) -> None:
        """
        Args:
            limit: Static int (≥1) or callable returning current limit (≥1).
                   Callable is evaluated on each acquire/release.
            gate_id: Optional identifier for logging/metrics.
                     If None, debug logging is suppressed.

        Raises:
            CapacityLimitError: If static limit is < 1.
        """
        if isinstance(limit, int):
            if limit < 1:
                raise CapacityLimitError(f"limit must be >= 1, got {limit}")

        self._limit_source = limit
        self._gate_id = gate_id
        self._active_count = 0
        self._holders: set[str] = set()
        self._waiters: deque[_GateWaiter] = deque()
        self._lock = asyncio.Lock()

        # Stats
        self._total_acquired = 0
        self._total_released = 0
        self._total_timeouts = 0
        self._total_cancellations = 0

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

    @property
    def queue_length(self) -> int:
        """Number of requests waiting."""
        return len(self._waiters)

    @property
    def holders(self) -> frozenset[str]:
        """Request IDs currently holding slots (read-only)."""
        return frozenset(self._holders)

    def try_acquire(self, request_id: str) -> bool:
        """
        Try to acquire slot without waiting (atomic).

        No await = no interleaving = safe without lock.

        Returns:
            True if slot acquired, False if at capacity.
        """
        try:
            limit = self.current_limit
        except CapacityLimitError as e:
            logger.error(
                "[GATE:%s] Dynamic limit invalid, cannot acquire: %s",
                self._gate_id,
                e,
            )
            return False

        if self._active_count < limit:
            self._active_count += 1
            self._holders.add(request_id)
            self._total_acquired += 1

            if self._gate_id is not None:
                logger.debug(
                    "🟢 [GATE:%s] %s acquired immediately (active=%d/%d)",
                    self._gate_id,
                    request_id[:8],
                    self._active_count,
                    limit,
                )
            return True
        return False

    async def acquire(
        self,
        request_id: str,
        timeout: float | None = None,
        cancellation_event: asyncio.Event | None = None,
    ) -> None:
        """
        Acquire slot, waiting FIFO if at capacity.

        Args:
            request_id: Identifier for logging and cancellation.
            timeout: Max seconds to wait (None = wait forever).
            cancellation_event: Optional event; if set, raises CancelledError.

        Raises:
            asyncio.TimeoutError: If timeout exceeded.
            asyncio.CancelledError: If cancellation_event set or cancel() called.
            CapacityLimitError: If dynamic limit returns invalid value.
        """
        # Fast path: try immediate acquire (no lock needed)
        if self.try_acquire(request_id):
            return

        # Slow path: must wait
        waiter: _GateWaiter | None = None

        async with self._lock:
            # Re-check under lock (limit may have changed or slot freed)
            limit = self.current_limit  # May raise CapacityLimitError

            if self._active_count < limit:
                self._active_count += 1
                self._holders.add(request_id)
                self._total_acquired += 1

                if self._gate_id is not None:
                    logger.debug(
                        "🟢 [GATE:%s] %s acquired on re-check (active=%d/%d)",
                        self._gate_id,
                        request_id[:8],
                        self._active_count,
                        limit,
                    )
                return

            # Must wait — create waiter
            waiter = _GateWaiter(
                request_id=request_id,
                future=asyncio.get_running_loop().create_future(),
                enqueue_time=monotonic(),
            )
            self._waiters.append(waiter)
            queue_position = len(self._waiters)

            if self._gate_id is not None:
                logger.info(
                    "🔶 [GATE:%s] %s queued at position %d (active=%d/%d)",
                    self._gate_id,
                    request_id[:8],
                    queue_position,
                    self._active_count,
                    limit,
                )

        # Wait outside lock
        try:
            await self._wait_for_slot(
                waiter.future,
                timeout=timeout,
                cancellation_event=cancellation_event,
            )

            # Successfully woken by release() — slot already transferred
            if self._gate_id is not None:
                wait_time = monotonic() - waiter.enqueue_time
                try:
                    limit = self.current_limit
                except CapacityLimitError:
                    limit = -1  # Telemetry failure, don't break acquire
                logger.info(
                    "🟢 [GATE:%s] %s woken after %.1fs (active=%d/%d)",
                    self._gate_id,
                    request_id[:8],
                    wait_time,
                    self._active_count,
                    limit,
                )

        except (TimeoutError, asyncio.CancelledError) as e:
            # Remove waiter from queue (if still there)
            await self._remove_waiter(waiter)

            if isinstance(e, TimeoutError):
                self._total_timeouts += 1
                if self._gate_id is not None:
                    logger.warning(
                        "⏰ [GATE:%s] %s timeout after %.1fs",
                        self._gate_id,
                        request_id[:8],
                        timeout,
                    )
            else:
                self._total_cancellations += 1
                if self._gate_id is not None:
                    logger.info(
                        "🛑 [GATE:%s] %s cancelled",
                        self._gate_id,
                        request_id[:8],
                    )
            raise

    async def _wait_for_slot(
        self,
        future: asyncio.Future[None],
        timeout: float | None,
        cancellation_event: asyncio.Event | None,
    ) -> None:
        """
        Wait for slot (future set by release()).

        Raises TimeoutError, CancelledError, or re-raises exception from future.
        """
        # Build awaitables: always the future, optionally cancellation_event
        future_awaitable = asyncio.ensure_future(future)
        awaitables: list[asyncio.Future] = [future_awaitable]
        cancel_task: asyncio.Task | None = None

        if cancellation_event is not None:
            cancel_task = asyncio.create_task(cancellation_event.wait())
            awaitables.append(cancel_task)

        try:
            done, pending = await asyncio.wait(
                awaitables,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cleanup pending
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            if not done:
                raise TimeoutError("Timeout waiting for capacity slot")

            if cancel_task is not None and cancel_task in done:
                raise asyncio.CancelledError("Cancelled via cancellation_event")

            # Check future result: propagate exceptions set by cancel()
            if future.cancelled():
                raise asyncio.CancelledError("Future was cancelled")
            if (exc := future.exception()) is not None:
                raise exc

        finally:
            if cancel_task is not None and not cancel_task.done():
                cancel_task.cancel()
                try:
                    await cancel_task
                except asyncio.CancelledError:
                    pass

    async def _remove_waiter(self, waiter: _GateWaiter) -> None:
        """Remove waiter from queue (if present)."""
        async with self._lock:
            try:
                self._waiters.remove(waiter)
            except ValueError:
                # Already removed (by release() or cancel())
                pass

    async def release(self, request_id: str | None = None) -> None:
        """
        Release slot — transfer FIFO or decrement.

        Must be called exactly once per successful acquire().

        Args:
            request_id: When provided, removes this holder before disposition.

        Raises:
            OverReleaseError: If called when active_count is 0.
        """
        async with self._lock:
            if request_id is not None and request_id not in self._holders:
                # Idempotent: force_release or a prior release already reclaimed.
                return

            # Invariant check FIRST: must have a slot to release
            if self._active_count == 0:
                raise OverReleaseError(
                    f"[GATE:{self._gate_id}] release() called but active_count=0. "
                    "This indicates a bug: release() called without matching acquire()."
                )

            if request_id is not None:
                self._holders.discard(request_id)

            self._total_released += 1

            # Transfer slot to oldest non-done waiter (FIFO)
            while self._waiters:
                waiter = self._waiters.popleft()
                if not waiter.future.done():
                    # Transfer ownership — don't decrement active_count
                    waiter.future.set_result(None)
                    self._holders.add(waiter.request_id)
                    self._total_acquired += 1  # Count as new acquisition

                    if self._gate_id is not None:
                        logger.debug(
                            "🔄 [GATE:%s] Slot transferred to %s (queued=%d)",
                            self._gate_id,
                            waiter.request_id[:8],
                            len(self._waiters),
                        )
                    return

            # No waiters — release the slot (active_count > 0 guaranteed by guard)
            self._active_count -= 1

            if self._gate_id is not None:
                try:
                    limit = self.current_limit
                except CapacityLimitError:
                    limit = -1  # Telemetry failure, don't break release
                logger.debug(
                    "🔓 [GATE:%s] Slot released (active=%d/%d)",
                    self._gate_id,
                    self._active_count,
                    limit,
                )

    async def force_release(self, request_id: str) -> bool:
        """Reclaim a holder's slot without a matching release() from that holder.

        Idempotent: returns False when ``request_id`` is not a current holder.
        Never raises ``OverReleaseError``.
        """
        async with self._lock:
            if request_id not in self._holders:
                return False

            self._holders.discard(request_id)
            self._total_released += 1

            while self._waiters:
                waiter = self._waiters.popleft()
                if not waiter.future.done():
                    waiter.future.set_result(None)
                    self._holders.add(waiter.request_id)
                    self._total_acquired += 1
                    if self._gate_id is not None:
                        logger.debug(
                            "🔄 [GATE:%s] force_release transferred to %s",
                            self._gate_id,
                            waiter.request_id[:8],
                        )
                    return True

            if self._active_count > 0:
                self._active_count -= 1
                if self._gate_id is not None:
                    logger.info(
                        "🔓 [GATE:%s] force_release reclaimed %s (active=%d)",
                        self._gate_id,
                        request_id[:8],
                        self._active_count,
                    )
            return True

    def set_limit(self, new_limit: int) -> None:
        """
        Update static limit (only valid if initialized with int limit).

        Args:
            new_limit: New capacity limit (must be >= 1).

        Raises:
            CapacityLimitError: If new_limit < 1 or limit is callable.
        """
        if callable(self._limit_source):
            raise CapacityLimitError(
                f"[GATE:{self._gate_id}] Cannot set_limit on gate with callable limit. "
                "Use callable to provide dynamic limit instead."
            )

        if new_limit < 1:
            logger.error(
                "[GATE:%s] Invalid capacity limit: %d",
                self._gate_id,
                new_limit,
            )
            raise CapacityLimitError(f"Limit must be >= 1, got {new_limit}")

        self._limit_source = new_limit

        if self._gate_id is not None:
            logger.info(
                "[GATE:%s] Limit updated to %d",
                self._gate_id,
                new_limit,
            )

    async def cancel(self, request_id: str) -> bool:
        """
        Cancel a waiting request by ID.

        Must acquire lock to safely modify _waiters deque that may be
        concurrently accessed by release().

        Sets CancelledError on the waiter's future so the awaiter
        receives the exception.

        Returns:
            True if found and cancelled, False if not found.
        """
        async with self._lock:
            for i, waiter in enumerate(self._waiters):
                if waiter.request_id == request_id:
                    # Remove from deque (safe: holding lock)
                    del self._waiters[i]

                    if not waiter.future.done():
                        waiter.future.set_exception(
                            asyncio.CancelledError(f"Request {request_id} cancelled")
                        )
                    self._total_cancellations += 1

                    if self._gate_id is not None:
                        logger.info(
                            "🛑 [GATE:%s] Cancelled %s",
                            self._gate_id,
                            request_id[:8],
                        )
                    return True
            return False

    @property
    def stats(self) -> GateStats:
        """Gate statistics."""
        try:
            limit = self.current_limit
        except CapacityLimitError as e:
            logger.error(
                "[GATE:%s] Dynamic limit invalid in stats: %s",
                self._gate_id,
                e,
            )
            limit = -1  # Indicate invalid

        return GateStats(
            active=self._active_count,
            limit=limit,
            queued=len(self._waiters),
            total_acquired=self._total_acquired,
            total_released=self._total_released,
            total_timeouts=self._total_timeouts,
            total_cancellations=self._total_cancellations,
        )
