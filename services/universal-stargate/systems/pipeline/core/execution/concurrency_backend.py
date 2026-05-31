"""Concurrency backend abstraction for pipeline per-key serialisation.

Phase A closure of cortex-chat-openai. The pipeline YAML surface
(``concurrency.key``, ``timeout_seconds``) is resolved by
``execution.concurrency.maybe_concurrency_gate`` and delegated to a
``ConcurrencyBackend`` instance owned by ``PipelineExecutor``.

In-process backend wraps :class:`libs.universal_concurrency.FifoCapacityGate`
at ``limit=1`` per resolved key, with explicit eviction on
release-when-idle so the per-process per-key gate store does not grow
unbounded.

Phase B / slice 1c extends the ABC with ``DistributedConcurrencyBackend``
(Redis-CAS or equivalent) without touching the YAML surface or the
``maybe_concurrency_gate`` call site.

Invariants:

- ∀ resolved_key: ∃! gate at any moment; gate is :class:`FifoCapacityGate`
  with ``limit=1`` (in-process backend).
- ∀ key, ∀ time t: ``active_count`` of the corresponding gate ≤ 1.
- ``release(key)`` MUST be paired with a successful ``acquire(key, ...)``;
  unpaired release raises (FifoCapacityGate's ``OverReleaseError``).
- Gate is evicted from the store iff ``active_count == 0 and queue_length == 0``
  observed under the store lock after release.
- Store mutations (get-or-create, evict) hold ``_store_lock`` to eliminate
  the check-then-act race between two acquirers seeing the same missing key.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from libs.universal_concurrency import FifoCapacityGate
from universal_logging import get_logger

logger = get_logger(__name__)


class ConcurrencyBackend(ABC):
    """Strategy interface for pipeline per-key serialisation.

    Implementations route ``acquire(key, ...) / release(key)`` calls to
    a per-key serialisation primitive (in-process FIFO gate, distributed
    Redis-CAS lock, etc.). The YAML surface and error shape
    (:class:`ConcurrencyLockTimeoutError`) are stable across backends.
    """

    @abstractmethod
    async def acquire(self, key: str, timeout: float, request_id: str) -> None:
        """Acquire serialisation slot on ``key``, blocking up to ``timeout``s.

        Raises ``TimeoutError`` when the slot cannot be acquired within
        the timeout window. Callers map this to the structured
        :class:`ConcurrencyLockTimeoutError` at the call site.

        Args:
            key: Resolved concurrency key (e.g. ``"chat:abc-123"``).
            timeout: Maximum seconds to wait for the slot.
            request_id: Caller identifier for FIFO ordering and logging
                (typically the pipeline execution_id).
        """

    @abstractmethod
    async def release(self, key: str) -> None:
        """Release the serialisation slot on ``key``.

        MUST be called exactly once per successful ``acquire(key, ...)``.
        Unpaired release surfaces a backend-defined error
        (``OverReleaseError`` for the in-process backend).
        """


class InProcessConcurrencyBackend(ConcurrencyBackend):
    """In-process per-key FIFO serialisation backed by :class:`FifoCapacityGate`.

    Owns ``dict[str, FifoCapacityGate]`` keyed by resolved key. Each
    gate is constructed at ``limit=1`` so it serialises exactly one
    holder at a time with FIFO fairness across queued waiters.

    Eviction policy: explicit. After every ``release(key)``, the store
    is checked under ``_store_lock``; if the gate's ``active_count``
    AND ``queue_length`` are both zero, the entry is removed. This
    guarantees that the per-key gate store does not accumulate stale
    entries when chat_ids fall out of use — a bound that distinguishes
    the in-process backend from a naive per-key primitive dict.

    Thread-safety: ``_store_lock`` is held only around dict mutations
    (get-or-create, evict). Gate acquisition and release run outside
    the store lock — the gate's own internal lock handles slot
    transfer and waiter wake.
    """

    def __init__(self) -> None:
        self._gates: dict[str, FifoCapacityGate] = {}
        self._store_lock: asyncio.Lock = asyncio.Lock()

    async def acquire(self, key: str, timeout: float, request_id: str) -> None:
        """Acquire a slot on the gate for ``key`` (creating the gate if absent).

        See :meth:`ConcurrencyBackend.acquire`. Raises ``TimeoutError``
        when the gate cannot grant the slot within ``timeout``.
        """
        gate = await self._get_or_create_gate(key)
        await gate.acquire(request_id, timeout=timeout)

    async def release(self, key: str) -> None:
        """Release the slot on the gate for ``key``; evict gate if idle.

        See :meth:`ConcurrencyBackend.release`. After release, evicts
        the gate from the store iff no holder and no queued waiters.
        """
        gate = self._gates.get(key)
        if gate is None:
            raise RuntimeError(
                f"InProcessConcurrencyBackend.release({key!r}): "
                "no gate exists for this key — release without acquire"
            )
        await gate.release()
        await self._maybe_evict(key)

    async def _get_or_create_gate(self, key: str) -> FifoCapacityGate:
        """Return the gate for ``key``, creating one at ``limit=1`` if absent.

        Holds ``_store_lock`` across the check-then-act so two
        concurrent acquirers on a fresh key see the same gate
        instance, not two independent ones.
        """
        async with self._store_lock:
            gate = self._gates.get(key)
            if gate is None:
                gate = FifoCapacityGate(limit=1, gate_id=f"pipeline-concurrency:{key}")
                self._gates[key] = gate
            return gate

    async def _maybe_evict(self, key: str) -> None:
        """Evict the gate for ``key`` iff it is idle and has no waiters.

        Idle is observed under ``_store_lock`` so a concurrent
        acquirer cannot race in between the check and the delete and
        lose its slot.
        """
        async with self._store_lock:
            gate = self._gates.get(key)
            if gate is None:
                return
            if gate.active_count == 0 and gate.queue_length == 0:
                del self._gates[key]
                logger.debug(
                    "InProcessConcurrencyBackend: evicted idle gate for key=%s",
                    key,
                )

    @property
    def gates_alive(self) -> int:
        """Snapshot count of gates currently in the store (debug/telemetry)."""
        return len(self._gates)


__all__ = ["ConcurrencyBackend", "InProcessConcurrencyBackend"]
