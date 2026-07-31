"""Tests for FifoCapacityGate.transfer_holder (park/restore primitive)."""

from __future__ import annotations

import asyncio

import pytest

from universal_concurrency import FifoCapacityGate, TransferHolderError


@pytest.mark.asyncio
async def test_transfer_holder_swaps_without_waking_waiters() -> None:
    gate = FifoCapacityGate(limit=1, gate_id="xfer")
    await gate.acquire("parent")
    assert gate.active_count == 1
    assert gate.holders == frozenset({"parent"})

    waiter_started = asyncio.Event()
    waiter_acquired = asyncio.Event()

    async def sibling_wait() -> None:
        waiter_started.set()
        await gate.acquire("sibling")
        waiter_acquired.set()

    task = asyncio.create_task(sibling_wait())
    await asyncio.wait_for(waiter_started.wait(), timeout=1.0)
    assert gate.queue_length == 1

    await gate.transfer_holder("parent", "child")
    await asyncio.sleep(0.05)
    assert not waiter_acquired.is_set()
    assert gate.active_count == 1
    assert gate.holders == frozenset({"child"})
    assert gate.queue_length == 1

    await gate.release("child")
    await asyncio.wait_for(waiter_acquired.wait(), timeout=1.0)
    await task
    assert gate.holders == frozenset({"sibling"})
    await gate.release("sibling")


@pytest.mark.asyncio
async def test_transfer_holder_raises_when_from_id_not_holder() -> None:
    gate = FifoCapacityGate(limit=1, gate_id="xfer")
    await gate.acquire("parent")
    with pytest.raises(TransferHolderError):
        await gate.transfer_holder("not-holder", "child")
    assert gate.holders == frozenset({"parent"})
    await gate.release("parent")


@pytest.mark.asyncio
async def test_try_acquire_idempotent_for_current_holder() -> None:
    gate = FifoCapacityGate(limit=1, gate_id="xfer")
    await gate.acquire("child")
    assert gate.try_acquire("child") is True
    assert gate.active_count == 1
    assert gate.holders == frozenset({"child"})
    await gate.release("child")
