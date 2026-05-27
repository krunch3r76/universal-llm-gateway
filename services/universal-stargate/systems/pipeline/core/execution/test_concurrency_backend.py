"""Unit tests for ``execution.concurrency_backend.InProcessConcurrencyBackend``.

Phase A closure of cortex-chat-openai. Covers the backend in isolation
(no pipeline / executor / context plumbing). Pipeline-level integration
via ``maybe_concurrency_gate`` lives in ``test_concurrency.py``.

Invariants covered:

- Serialization: two concurrent acquires on the same key serialise.
- FIFO fairness: queued waiters wake in arrival order.
- Independent keys: distinct keys do not block each other.
- Timeout: acquire raises ``TimeoutError`` when slot cannot be obtained
  within the timeout window.
- TTL eviction: gate is removed from the store when active_count and
  queue_length both reach zero.
- Release-without-acquire: raises (no silent over-release).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_repo_root = str(Path(__file__).resolve().parents[5])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from systems.pipeline.core.execution.concurrency_backend import (  # noqa: E402
    InProcessConcurrencyBackend,
)


@pytest.mark.asyncio
async def test_acquire_release_evicts_gate() -> None:
    """Single acquire/release pair leaves the store empty (TTL eviction)."""
    backend = InProcessConcurrencyBackend()
    assert backend.gates_alive == 0

    await backend.acquire("chat:abc", timeout=5.0, request_id="req-1")
    assert backend.gates_alive == 1

    await backend.release("chat:abc")
    assert backend.gates_alive == 0, "idle gate must be evicted on release"


@pytest.mark.asyncio
async def test_concurrent_same_key_serialises() -> None:
    """Two acquires on the same key run sequentially (FIFO)."""
    backend = InProcessConcurrencyBackend()
    log: list[str] = []

    async def worker(label: str, hold_for: float) -> None:
        await backend.acquire("chat:abc", timeout=5.0, request_id=label)
        log.append(f"{label}:enter")
        await asyncio.sleep(hold_for)
        log.append(f"{label}:exit")
        await backend.release("chat:abc")

    task_a = asyncio.create_task(worker("A", 0.05))
    await asyncio.sleep(0.01)
    task_b = asyncio.create_task(worker("B", 0.01))
    await asyncio.gather(task_a, task_b)

    assert log == ["A:enter", "A:exit", "B:enter", "B:exit"]
    assert backend.gates_alive == 0


@pytest.mark.asyncio
async def test_distinct_keys_do_not_block() -> None:
    """Acquires on distinct keys run concurrently."""
    backend = InProcessConcurrencyBackend()
    enter_b = asyncio.Event()

    async def hold_a() -> None:
        await backend.acquire("chat:alice", timeout=5.0, request_id="A")
        await enter_b.wait()
        await backend.release("chat:alice")

    async def hold_b() -> None:
        await backend.acquire("chat:bob", timeout=5.0, request_id="B")
        # B can enter even though A still holds its own key.
        enter_b.set()
        await backend.release("chat:bob")

    await asyncio.gather(hold_a(), hold_b())
    assert backend.gates_alive == 0


@pytest.mark.asyncio
async def test_acquire_timeout_raises_timeout_error() -> None:
    """Second acquirer exhausting the timeout raises ``TimeoutError``.

    Caller (``maybe_concurrency_gate``) maps this to the structured
    ``ConcurrencyLockTimeoutError``; the backend surface is the raw
    asyncio exception.
    """
    backend = InProcessConcurrencyBackend()
    holder_release = asyncio.Event()

    async def holder() -> None:
        await backend.acquire("chat:abc", timeout=5.0, request_id="holder")
        await holder_release.wait()
        await backend.release("chat:abc")

    holder_task = asyncio.create_task(holder())
    await asyncio.sleep(0.01)  # give holder time to acquire

    with pytest.raises(TimeoutError):
        await backend.acquire("chat:abc", timeout=0.05, request_id="waiter")

    holder_release.set()
    await holder_task
    assert backend.gates_alive == 0


@pytest.mark.asyncio
async def test_release_without_acquire_raises() -> None:
    """Release on a key with no gate raises (fail-loud)."""
    backend = InProcessConcurrencyBackend()
    with pytest.raises(RuntimeError, match="release without acquire"):
        await backend.release("chat:nonexistent")


@pytest.mark.asyncio
async def test_gate_retained_while_waiters_queued() -> None:
    """Gate is NOT evicted while a waiter is queued (eviction guard)."""
    backend = InProcessConcurrencyBackend()
    holder_release = asyncio.Event()
    waiter_started = asyncio.Event()

    async def holder() -> None:
        await backend.acquire("chat:abc", timeout=5.0, request_id="holder")
        waiter_started.clear()
        await holder_release.wait()
        # At this moment, the waiter is queued. Release transfers the
        # slot to the waiter — gate must NOT be evicted (queue_length
        # was 1, then transfer leaves active_count=1).
        await backend.release("chat:abc")

    async def waiter() -> None:
        waiter_started.set()
        await backend.acquire("chat:abc", timeout=5.0, request_id="waiter")
        # We hold the slot now — store still has the gate.
        assert backend.gates_alive == 1
        await backend.release("chat:abc")

    holder_task = asyncio.create_task(holder())
    await asyncio.sleep(0.01)
    waiter_task = asyncio.create_task(waiter())
    await asyncio.sleep(0.01)  # waiter enqueues
    holder_release.set()
    await asyncio.gather(holder_task, waiter_task)

    assert backend.gates_alive == 0, "gate must be evicted after last release"
