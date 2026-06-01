"""Per-source FIFO serialization for index/delete (replaces dict[str, asyncio.Lock])."""

from __future__ import annotations

import asyncio
import uuid

from universal_concurrency import FifoCapacityGate

_gates: dict[str, FifoCapacityGate] = {}
_store_lock = asyncio.Lock()


async def acquire_source_path(source: str, *, timeout: float | None = None) -> None:
    """Serialize concurrent index/delete for the same resolved source path."""
    gate = await _get_or_create_gate(source)
    request_id = f"rag-index:{source}:{uuid.uuid4().hex[:8]}"
    await gate.acquire(request_id, timeout=timeout)


async def release_source_path(source: str) -> None:
    """Release the per-source gate slot; evict idle gates from the store."""
    gate = _gates.get(source)
    if gate is None:
        raise RuntimeError(
            f"release_source_path({source!r}): no gate — release without acquire"
        )
    await gate.release()
    await _maybe_evict(source)


async def _get_or_create_gate(source: str) -> FifoCapacityGate:
    async with _store_lock:
        gate = _gates.get(source)
        if gate is None:
            gate = FifoCapacityGate(limit=1, gate_id=f"rag-file-index:{source}")
            _gates[source] = gate
        return gate


async def _maybe_evict(source: str) -> None:
    async with _store_lock:
        gate = _gates.get(source)
        if gate is None:
            return
        if gate.active_count == 0 and gate.queue_length == 0:
            del _gates[source]
