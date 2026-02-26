"""
Regression tests for CapacityPool — specifically the slot leak on cancellation.

Scenario: _dispatch admits a waiter (pops from queue, increments in_flight,
sets future result), then the waiter's task is cancelled before CapacityToken
is created.  Without the fix, in_flight is permanently leaked.
"""

import asyncio
import sys
from pathlib import Path

import pytest

# Ensure the stargate service root is importable
_stargate_root = str(Path(__file__).resolve().parents[3])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from systems.routing.capacity.pool import CapacityPool, CapacityToken


@pytest.fixture
def pool() -> CapacityPool:
    return CapacityPool(event_bus=None)


@pytest.fixture
def seeded_pool(pool: CapacityPool) -> CapacityPool:
    """Pool with edge-jupiter-gateway/hermes3 capacity=1."""
    pool.set_capacity("edge-jupiter-gateway", "hermes3", 1)
    return pool


# ── Basic token lifecycle ──


@pytest.mark.asyncio
async def test_acquire_and_release(seeded_pool: CapacityPool) -> None:
    token = await seeded_pool.acquire_token(
        request_id="req-1",
        model_id="hermes3",
        allowed_gateway_ids=frozenset({"edge-jupiter-gateway"}),
    )
    assert isinstance(token, CapacityToken)
    assert token.gateway_id == "edge-jupiter-gateway"
    assert not token.queued

    avail, in_flight, cap = seeded_pool.get_slot_info("edge-jupiter-gateway", "hermes3")
    assert in_flight == 1
    assert cap == 1

    await token.release()
    avail, in_flight, cap = seeded_pool.get_slot_info("edge-jupiter-gateway", "hermes3")
    assert in_flight == 0


@pytest.mark.asyncio
async def test_idempotent_release(seeded_pool: CapacityPool) -> None:
    token = await seeded_pool.acquire_token(
        request_id="req-1",
        model_id="hermes3",
        allowed_gateway_ids=frozenset({"edge-jupiter-gateway"}),
    )
    await token.release()
    await token.release()  # no-op, no error
    _, in_flight, _ = seeded_pool.get_slot_info("edge-jupiter-gateway", "hermes3")
    assert in_flight == 0


# ── Queuing ──


@pytest.mark.asyncio
async def test_second_request_queues(seeded_pool: CapacityPool) -> None:
    """With capacity=1, second request must queue."""
    token_a = await seeded_pool.acquire_token(
        request_id="req-a",
        model_id="hermes3",
        allowed_gateway_ids=frozenset({"edge-jupiter-gateway"}),
    )

    async def acquire_b() -> CapacityToken:
        return await seeded_pool.acquire_token(
            request_id="req-b",
            model_id="hermes3",
            allowed_gateway_ids=frozenset({"edge-jupiter-gateway"}),
        )

    task_b = asyncio.create_task(acquire_b())
    await asyncio.sleep(0.01)  # let B queue

    snap = seeded_pool.get_snapshot()
    assert snap["total_queued"] == 1

    await token_a.release()
    token_b = await asyncio.wait_for(task_b, timeout=1.0)
    assert token_b.queued
    assert token_b.gateway_id == "edge-jupiter-gateway"

    await token_b.release()
    _, in_flight, _ = seeded_pool.get_slot_info("edge-jupiter-gateway", "hermes3")
    assert in_flight == 0


# ── CRITICAL: Slot leak on cancellation ──


@pytest.mark.asyncio
async def test_cancel_admitted_waiter_recovers_slot(seeded_pool: CapacityPool) -> None:
    """
    Regression: cancelling a waiter AFTER dispatch admitted it (incremented
    in_flight, set future) must recover the slot.

    Scenario: Two simultaneous requests, capacity=1.
    - req-a acquires the slot
    - req-b queues
    - req-a releases → dispatch admits req-b (in_flight=1)
    - req-b's task is cancelled before CapacityToken is created
    - in_flight MUST be decremented back to 0
    """
    token_a = await seeded_pool.acquire_token(
        request_id="req-a",
        model_id="hermes3",
        allowed_gateway_ids=frozenset({"edge-jupiter-gateway"}),
    )

    async def acquire_b() -> CapacityToken:
        return await seeded_pool.acquire_token(
            request_id="req-b",
            model_id="hermes3",
            allowed_gateway_ids=frozenset({"edge-jupiter-gateway"}),
        )

    task_b = asyncio.create_task(acquire_b())
    await asyncio.sleep(0.01)  # let B enter queue

    # Cancel B's task BEFORE releasing A (B will be cancelled while awaiting)
    task_b.cancel()

    # Release A — dispatch sets B's future result, but B's task is cancelled
    await token_a.release()

    # Let the event loop process the cancellation
    await asyncio.sleep(0.05)

    # B should have been cancelled
    assert task_b.cancelled()

    # CRITICAL: in_flight must be 0, not stuck at 1
    avail, in_flight, cap = seeded_pool.get_slot_info(
        "edge-jupiter-gateway", "hermes3"
    )
    assert in_flight == 0, (
        f"Slot leak: in_flight={in_flight} after cancellation "
        f"(expected 0, capacity={cap})"
    )


@pytest.mark.asyncio
async def test_cancel_admitted_waiter_dispatches_next(
    seeded_pool: CapacityPool,
) -> None:
    """
    After recovering a leaked slot from a cancelled waiter, the next
    queued waiter must be admitted.
    """
    token_a = await seeded_pool.acquire_token(
        request_id="req-a",
        model_id="hermes3",
        allowed_gateway_ids=frozenset({"edge-jupiter-gateway"}),
    )

    async def acquire(rid: str) -> CapacityToken:
        return await seeded_pool.acquire_token(
            request_id=rid,
            model_id="hermes3",
            allowed_gateway_ids=frozenset({"edge-jupiter-gateway"}),
        )

    task_b = asyncio.create_task(acquire("req-b"))
    task_c = asyncio.create_task(acquire("req-c"))
    await asyncio.sleep(0.01)

    # Cancel B before A releases
    task_b.cancel()

    # Release A → dispatch admits B (cancelled) → recover → dispatch admits C
    await token_a.release()
    await asyncio.sleep(0.05)

    # C should eventually be admitted
    token_c = await asyncio.wait_for(task_c, timeout=1.0)
    assert token_c.gateway_id == "edge-jupiter-gateway"
    _, in_flight, _ = seeded_pool.get_slot_info("edge-jupiter-gateway", "hermes3")
    assert in_flight == 1

    await token_c.release()
    _, in_flight, _ = seeded_pool.get_slot_info("edge-jupiter-gateway", "hermes3")
    assert in_flight == 0


@pytest.mark.asyncio
async def test_cancel_queued_waiter_not_admitted(seeded_pool: CapacityPool) -> None:
    """Cancelling a waiter that was NOT yet admitted (still in queue) is clean."""
    token_a = await seeded_pool.acquire_token(
        request_id="req-a",
        model_id="hermes3",
        allowed_gateway_ids=frozenset({"edge-jupiter-gateway"}),
    )

    async def acquire_b() -> CapacityToken:
        return await seeded_pool.acquire_token(
            request_id="req-b",
            model_id="hermes3",
            allowed_gateway_ids=frozenset({"edge-jupiter-gateway"}),
        )

    task_b = asyncio.create_task(acquire_b())
    await asyncio.sleep(0.01)

    # Cancel B while A still holds the slot (B was never admitted)
    task_b.cancel()
    await asyncio.sleep(0.01)
    assert task_b.cancelled()

    # Queue should be empty (B was removed)
    snap = seeded_pool.get_snapshot()
    assert snap["total_queued"] == 0

    # A's slot still held
    _, in_flight, _ = seeded_pool.get_slot_info("edge-jupiter-gateway", "hermes3")
    assert in_flight == 1

    await token_a.release()
    _, in_flight, _ = seeded_pool.get_slot_info("edge-jupiter-gateway", "hermes3")
    assert in_flight == 0


@pytest.mark.asyncio
async def test_timeout_admitted_waiter_recovers_slot(seeded_pool: CapacityPool) -> None:
    """TimeoutError after admission also recovers the slot."""
    token_a = await seeded_pool.acquire_token(
        request_id="req-a",
        model_id="hermes3",
        allowed_gateway_ids=frozenset({"edge-jupiter-gateway"}),
    )

    async def acquire_b_with_timeout() -> CapacityToken:
        return await seeded_pool.acquire_token(
            request_id="req-b",
            model_id="hermes3",
            allowed_gateway_ids=frozenset({"edge-jupiter-gateway"}),
            timeout_s=0.05,
        )

    task_b = asyncio.create_task(acquire_b_with_timeout())
    await asyncio.sleep(0.01)

    # Wait for B to timeout (0.05s)
    with pytest.raises(TimeoutError):
        await task_b

    # Release A — no leak from B's timeout
    await token_a.release()
    _, in_flight, _ = seeded_pool.get_slot_info("edge-jupiter-gateway", "hermes3")
    assert in_flight == 0
