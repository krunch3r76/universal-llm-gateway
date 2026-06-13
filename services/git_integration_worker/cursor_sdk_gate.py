"""FIFO capacity gate for cursor-sdk dispatches.

The slot is a dispatch/thread-lifetime lease. A timed-out outer coroutine must
not release capacity while the non-cancellable worker thread is still running.
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
    req_id = dispatch_id or str(uuid.uuid4())
    await _GATE.acquire(req_id)
    return req_id


async def release_sdk_dispatch_slot() -> None:
    await _GATE.release()


def release_sdk_dispatch_slot_sync(loop: asyncio.AbstractEventLoop) -> None:
    """Release the slot from a worker thread via the owning event loop."""
    fut = asyncio.run_coroutine_threadsafe(release_sdk_dispatch_slot(), loop)
    fut.result(timeout=30.0)


def sdk_dispatch_gate_stats() -> dict[str, int]:
    return {
        "active": _GATE.active_count,
        "queued": _GATE.queue_length,
        "limit": _GATE.current_limit,
    }
