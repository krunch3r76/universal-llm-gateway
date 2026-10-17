"""K3 — FIFO weighted contextualize budget (B1 fix)."""

from __future__ import annotations

import asyncio

import pytest

from services.rag.contextualize_budget import (
    CONTEXTUALIZE_GLOBAL_MAX,
    ContextualizeWeightedBudget,
    reset_contextualize_budget_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_budget() -> None:
    reset_contextualize_budget_for_tests()
    yield
    reset_contextualize_budget_for_tests()


@pytest.mark.asyncio
async def test_fifo_large_waiter_served_before_later_small() -> None:
    budget = ContextualizeWeightedBudget(CONTEXTUALIZE_GLOBAL_MAX)
    release_holder = asyncio.Event()

    async def holder() -> None:
        await budget.acquire(8)
        await release_holder.wait()
        await budget.release(8)

    holder_task = asyncio.create_task(holder())
    await asyncio.sleep(0)
    large_task = asyncio.create_task(budget.acquire(8))
    await asyncio.sleep(0)
    small_task = asyncio.create_task(budget.acquire(1))
    release_holder.set()
    await holder_task
    await asyncio.wait_for(large_task, timeout=1.0)
    await budget.release(8)
    await asyncio.wait_for(small_task, timeout=1.0)
    await budget.release(1)


@pytest.mark.asyncio
async def test_cancellation_does_not_leak_permits() -> None:
    budget = ContextualizeWeightedBudget(CONTEXTUALIZE_GLOBAL_MAX)
    await budget.acquire(8)

    async def waiter() -> None:
        await budget.acquire(4)

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await budget.release(8)
    await budget.acquire(8)
    assert budget.inflight_permits == 8
