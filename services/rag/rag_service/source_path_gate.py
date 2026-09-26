"""Per-source FIFO serialization for index/delete (replaces dict[str, asyncio.Lock]).

``acquire_source_path`` / ``release_source_path`` wrap one
``FifoCapacityGate(limit=1)`` per resolved source path so concurrent
``indexing._index_file`` and ``indexing._delete_file`` calls on the same file run
one at a time in arrival order. Gates are created lazily under a module lock and
evicted once they have no active holder and no queued waiters, so the gate map
does not grow with the corpus.
"""

from __future__ import annotations

import asyncio
import uuid

from universal_concurrency import FifoCapacityGate

_gates: dict[str, FifoCapacityGate] = {}
_store_lock = asyncio.Lock()


async def acquire_source_path(source: str, *, timeout: float | None = None) -> None:
    """Wait for exclusive FIFO access to one resolved source path before index/delete.

    Lazily creates the per-source ``FifoCapacityGate`` (limit 1) and enqueues a
    unique ``rag-index:<source>:<id>`` request. Callers (``indexing._index_file``,
    ``indexing._delete_file``) must pair it with ``release_source_path`` in a
    ``finally`` block. ``timeout`` (seconds, None waits forever) is passed to
    the gate, which raises if it expires before the slot is granted.
    """
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
