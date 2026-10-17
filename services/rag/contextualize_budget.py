"""FIFO weighted permit budget for rag contextualize pipeline calls (cap 8)."""

from __future__ import annotations

import asyncio
import collections
from contextlib import asynccontextmanager
from dataclasses import dataclass

CONTEXTUALIZE_GLOBAL_MAX = 8


@dataclass
class _BudgetWaiter:
    permits: int
    future: asyncio.Future[None]


class ContextualizeWeightedBudget:
    """Strict-FIFO weighted semaphore: no barging past a head waiter that does not fit."""

    def __init__(self, capacity: int = CONTEXTUALIZE_GLOBAL_MAX) -> None:
        self._capacity = capacity
        self._available = capacity
        self._inflight = 0
        self._waiters: collections.deque[_BudgetWaiter] = collections.deque()

    @property
    def inflight_permits(self) -> int:
        return self._inflight

    def reset_for_tests(self) -> None:
        self._available = self._capacity
        self._inflight = 0
        self._waiters.clear()

    async def acquire(self, permits: int) -> None:
        if permits <= 0:
            return
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[None] = loop.create_future()
        granted = False
        if not self._waiters and self._available >= permits:
            self._available -= permits
            self._inflight += permits
            granted = True
        else:
            self._waiters.append(_BudgetWaiter(permits=permits, future=fut))
        if granted:
            return
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                self._available += permits
                self._inflight -= permits
            else:
                self._drop_waiter(fut)
            self._wake_waiters_locked()
            raise

    def release(self, permits: int) -> None:
        if permits <= 0:
            return
        self._available += permits
        self._inflight -= permits
        self._wake_waiters_locked()

    def _drop_waiter(self, fut: asyncio.Future[None]) -> None:
        for idx, waiter in enumerate(self._waiters):
            if waiter.future is fut:
                del self._waiters[idx]
                return

    def _wake_waiters_locked(self) -> None:
        while self._waiters:
            head = self._waiters[0]
            if head.future.cancelled():
                self._waiters.popleft()
                continue
            if self._available < head.permits:
                break
            self._waiters.popleft()
            self._available -= head.permits
            self._inflight += head.permits
            if not head.future.done():
                head.future.set_result(None)


_budget = ContextualizeWeightedBudget()


def contextualize_inflight_permits() -> int:
    return _budget.inflight_permits


def reset_contextualize_budget_for_tests() -> None:
    _budget.reset_for_tests()


@asynccontextmanager
async def contextualize_weighted_budget(chunk_count: int):
    """Acquire min(chunks, cap) permits; release in finally."""
    permits = min(chunk_count, CONTEXTUALIZE_GLOBAL_MAX)
    await _budget.acquire(permits)
    try:
        yield permits
    finally:
        _budget.release(permits)
