"""FIFO serializer for cursor-sdk dispatches on the shared source checkout.

Temporary until per-dispatch worktrees isolate parallel SDK sessions.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from universal_concurrency import FifoCapacityGate

_GATE = FifoCapacityGate(limit=1, gate_id="cursor-sdk-dispatch")


@asynccontextmanager
async def sdk_dispatch_slot(*, dispatch_id: str | None = None) -> AsyncIterator[None]:
    req_id = dispatch_id or str(uuid.uuid4())
    await _GATE.acquire(req_id)
    try:
        yield
    finally:
        await _GATE.release()


def sdk_dispatch_gate_stats() -> dict[str, int]:
    return {
        "active": _GATE.active_count,
        "queued": _GATE.queue_length,
    }
