"""Tests for FifoCapacityGate.force_release holder reconciliation."""

from __future__ import annotations

import asyncio

import pytest

from universal_concurrency import FifoCapacityGate


@pytest.mark.asyncio
async def test_force_release_reclaims_dead_holder() -> None:
    gate = FifoCapacityGate(limit=1, gate_id="test")
    await gate.acquire("dead-holder")
    assert gate.active_count == 1
    assert "dead-holder" in gate.holders

    reclaimed = await gate.force_release("dead-holder")
    assert reclaimed is True
    assert gate.active_count == 0
    assert "dead-holder" not in gate.holders


@pytest.mark.asyncio
async def test_force_release_transfers_to_waiter() -> None:
    gate = FifoCapacityGate(limit=1, gate_id="test")
    await gate.acquire("holder-a")

    waiter_started = asyncio.Event()
    waiter_done = asyncio.Event()

    async def wait_for_slot() -> None:
        waiter_started.set()
        await gate.acquire("waiter-b")
        waiter_done.set()

    task = asyncio.create_task(wait_for_slot())
    await asyncio.wait_for(waiter_started.wait(), timeout=1.0)
    assert gate.queue_length == 1

    reclaimed = await gate.force_release("holder-a")
    assert reclaimed is True
    await asyncio.wait_for(waiter_done.wait(), timeout=1.0)
    await task

    assert gate.active_count == 1
    assert "holder-a" not in gate.holders
    assert "waiter-b" in gate.holders


@pytest.mark.asyncio
async def test_force_release_unknown_id_is_noop() -> None:
    gate = FifoCapacityGate(limit=1, gate_id="test")
    await gate.acquire("known")
    assert gate.active_count == 1

    reclaimed = await gate.force_release("unknown")
    assert reclaimed is False
    assert gate.active_count == 1
    assert "known" in gate.holders

    await gate.release("known")
    assert gate.active_count == 0


@pytest.mark.asyncio
async def test_release_idempotent_after_force_release() -> None:
    gate = FifoCapacityGate(limit=1, gate_id="test")
    await gate.acquire("dispatch-1")
    await gate.force_release("dispatch-1")
    assert gate.active_count == 0

    await gate.release("dispatch-1")
    assert gate.active_count == 0
