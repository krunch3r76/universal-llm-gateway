"""FIFO capacity gate for cursor-sdk dispatches.

The slot is a dispatch/thread-lifetime lease. A timed-out outer coroutine must
not release capacity while the non-cancellable worker thread is still running.
Nest park/restore uses ``transfer_holder`` so siblings cannot steal the slot.
"""

from __future__ import annotations

import asyncio
import os
import uuid

from universal_concurrency import FifoCapacityGate


def _limit() -> int:
    raw = os.environ.get("CURSOR_SDK_DISPATCH_CONCURRENCY", "1")
    return max(1, int(raw))


_GATE = FifoCapacityGate(limit=_limit, gate_id="cursor-sdk-dispatch")


async def acquire_sdk_dispatch_slot(*, dispatch_id: str | None = None) -> str:
    """Acquire the cursor-sdk FIFO capacity slot for ``dispatch_id``.

    Idempotent when ``dispatch_id`` already holds (nest park transfer path).
    Returns the holder id used for a matching release/transfer.
    """
    req_id = dispatch_id or str(uuid.uuid4())
    await _GATE.acquire(req_id)
    return req_id


async def release_sdk_dispatch_slot(*, dispatch_id: str) -> None:
    """Release the cursor-sdk capacity slot, waking the next FIFO waiter if any."""
    await _GATE.release(dispatch_id)


def release_sdk_dispatch_slot_sync(
    loop: asyncio.AbstractEventLoop, *, dispatch_id: str
) -> None:
    """Release the slot from a worker thread via the owning event loop."""
    fut = asyncio.run_coroutine_threadsafe(
        release_sdk_dispatch_slot(dispatch_id=dispatch_id), loop
    )
    fut.result(timeout=30.0)


async def force_release_sdk_dispatch_slot(*, dispatch_id: str) -> bool:
    """Reclaim a holder slot without requiring a matching release from that holder.

    Idempotent: returns False when ``dispatch_id`` is not a current holder.
    """
    return await _GATE.force_release(dispatch_id)


def force_release_sdk_dispatch_slot_sync(
    loop: asyncio.AbstractEventLoop, *, dispatch_id: str
) -> bool:
    """Thread-safe ``force_release_sdk_dispatch_slot`` via the owning event loop."""
    fut = asyncio.run_coroutine_threadsafe(
        force_release_sdk_dispatch_slot(dispatch_id=dispatch_id), loop
    )
    return fut.result(timeout=30.0)


async def transfer_sdk_dispatch_slot(*, from_id: str, to_id: str) -> None:
    """Park/restore capacity handoff — no waiter wake; ``active_count`` unchanged."""
    await _GATE.transfer_holder(from_id, to_id)


def transfer_sdk_dispatch_slot_sync(
    loop: asyncio.AbstractEventLoop, *, from_id: str, to_id: str
) -> None:
    """Thread-safe ``transfer_sdk_dispatch_slot`` via the owning event loop."""
    fut = asyncio.run_coroutine_threadsafe(
        transfer_sdk_dispatch_slot(from_id=from_id, to_id=to_id), loop
    )
    fut.result(timeout=30.0)


def sdk_dispatch_gate_stats() -> dict[str, int]:
    """Return active/queued/limit counters for the cursor-sdk capacity gate."""
    return {
        "active": _GATE.active_count,
        "queued": _GATE.queue_length,
        "limit": _GATE.current_limit,
    }


def sdk_dispatch_gate_holders() -> frozenset[str]:
    """Return the current capacity holder dispatch ids (tests and live probes)."""
    return _GATE.holders
