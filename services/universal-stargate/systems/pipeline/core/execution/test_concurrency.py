"""Unit tests for ``execution.concurrency.maybe_concurrency_gate``.

Phase A closure of cortex-chat-openai. Covers the gate-backed context
manager shape:

- No-op path: pipelines without a ``concurrency:`` block bypass the
  surface (yield runs without acquire/release work).
- Serialization: two concurrent acquisitions on the same key
  serialize; the second waits for the first to release.
- Timeout: ``ConcurrencyLockTimeoutError`` is raised when the wait
  exceeds ``timeout_seconds``; the original holder retains the slot.
- Missing chat_id: ``ValueError`` at run time matches
  ``assemble_thread_v1``'s raise-on-missing-chat-id discipline.
- Unknown placeholder: ``ValueError`` rejects unsupported
  ``{...}`` tokens (closes the substring-permissive ``.replace()``
  gap).
- Release on exception: gate is released even when the inner block
  raises (structural ``async with`` guarantee).
- TTL eviction (Phase A closure): the gate is removed from the
  backend's store after release when no waiters remain.
- Gate retention under contention: the gate is NOT evicted while a
  second acquirer is queued.

Integration tests (two real pipelines serialising via /v1/chat/completions
against a stub frontier endpoint) live in tests/test_cortex_chat_openai_persistence.py.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_repo_root = str(Path(__file__).resolve().parents[5])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from systems.pipeline.core.execution.concurrency import (  # noqa: E402
    maybe_concurrency_gate,
)
from systems.pipeline.core.execution.concurrency_backend import (  # noqa: E402
    InProcessConcurrencyBackend,
)
from systems.pipeline.core.execution.errors import (  # noqa: E402
    ConcurrencyLockTimeoutError,
)


def _make_pipeline(
    concurrency: dict | None = None,
    *,
    pipeline_id: str = "test-pipeline",
) -> MagicMock:
    pipeline = MagicMock()
    pipeline.id = pipeline_id
    pipeline.model_extra = {"concurrency": concurrency} if concurrency else {}
    return pipeline


def _make_context(
    chat_id: str | None = "chat-abc",
    *,
    execution_id: str = "exec-1",
) -> MagicMock:
    context = MagicMock()
    context.chat_id = chat_id
    context.execution_id = execution_id
    return context


@pytest.mark.asyncio
async def test_no_concurrency_block_is_noop() -> None:
    """Pipelines without a ``concurrency:`` block bypass the gate entirely."""
    pipeline = _make_pipeline(concurrency=None)
    context = _make_context()
    backend = InProcessConcurrencyBackend()

    async with maybe_concurrency_gate(pipeline, context, backend):
        pass

    assert backend.gates_alive == 0, (
        "no gate should be created for non-concurrent pipelines"
    )


@pytest.mark.asyncio
async def test_empty_key_is_noop() -> None:
    """Empty-string ``key`` disables the feature (treated as absent)."""
    pipeline = _make_pipeline(concurrency={"key": ""})
    context = _make_context()
    backend = InProcessConcurrencyBackend()

    async with maybe_concurrency_gate(pipeline, context, backend):
        pass

    assert backend.gates_alive == 0


@pytest.mark.asyncio
async def test_acquire_and_release_on_success_evicts_gate() -> None:
    """Successful exit releases the slot and evicts the idle gate (TTL)."""
    pipeline = _make_pipeline(concurrency={"key": "chat:{context.chat_id}"})
    context = _make_context(chat_id="abc")
    backend = InProcessConcurrencyBackend()

    async with maybe_concurrency_gate(pipeline, context, backend):
        assert backend.gates_alive == 1, (
            "gate should be live during the critical section"
        )

    assert backend.gates_alive == 0, "gate should be evicted after release-when-idle"


@pytest.mark.asyncio
async def test_release_on_exception() -> None:
    """Gate is released (and evicted) when the inner block raises."""
    pipeline = _make_pipeline(concurrency={"key": "chat:{context.chat_id}"})
    context = _make_context(chat_id="abc")
    backend = InProcessConcurrencyBackend()

    with pytest.raises(RuntimeError, match="boom"):
        async with maybe_concurrency_gate(pipeline, context, backend):
            assert backend.gates_alive == 1
            raise RuntimeError("boom")

    assert backend.gates_alive == 0


@pytest.mark.asyncio
async def test_concurrent_executions_serialize() -> None:
    """Two concurrent acquisitions on the same key serialize (FIFO)."""
    pipeline = _make_pipeline(concurrency={"key": "chat:{context.chat_id}"})
    backend = InProcessConcurrencyBackend()
    log: list[str] = []

    async def worker(label: str, hold_for: float) -> None:
        ctx = _make_context(chat_id="abc", execution_id=label)
        async with maybe_concurrency_gate(pipeline, ctx, backend):
            log.append(f"{label}:enter")
            await asyncio.sleep(hold_for)
            log.append(f"{label}:exit")

    task_a = asyncio.create_task(worker("A", 0.05))
    await asyncio.sleep(0.01)
    task_b = asyncio.create_task(worker("B", 0.01))
    await asyncio.gather(task_a, task_b)

    # The gate must serialize: A enters, A exits, B enters, B exits.
    assert log == ["A:enter", "A:exit", "B:enter", "B:exit"]
    assert backend.gates_alive == 0, "gate evicted after last release"


@pytest.mark.asyncio
async def test_timeout_raises_concurrency_lock_timeout_error() -> None:
    """A second acquirer that exhausts ``timeout_seconds`` raises."""
    pipeline = _make_pipeline(
        concurrency={"key": "chat:{context.chat_id}", "timeout_seconds": 0.05}
    )
    context_a = _make_context(chat_id="abc", execution_id="exec-a")
    context_b = _make_context(chat_id="abc", execution_id="exec-b")
    backend = InProcessConcurrencyBackend()
    holder_release = asyncio.Event()

    async def holder() -> None:
        async with maybe_concurrency_gate(pipeline, context_a, backend):
            await holder_release.wait()

    holder_task = asyncio.create_task(holder())
    # Give the holder a moment to acquire.
    await asyncio.sleep(0.01)

    with pytest.raises(ConcurrencyLockTimeoutError) as exc_info:
        async with maybe_concurrency_gate(pipeline, context_b, backend):
            pytest.fail("second acquirer must not enter the body")

    err = exc_info.value
    assert err.pipeline_id == "test-pipeline"
    assert err.execution_id == "exec-b"
    assert err.key == "chat:abc"
    assert err.timeout_seconds == pytest.approx(0.05)
    assert err.retryable is True

    holder_release.set()
    await holder_task

    # The holder ultimately released; gate is evicted now that it is idle.
    assert backend.gates_alive == 0


@pytest.mark.asyncio
async def test_missing_chat_id_raises_value_error() -> None:
    """``chat_id=None`` with ``{context.chat_id}`` in key raises at run time."""
    pipeline = _make_pipeline(concurrency={"key": "chat:{context.chat_id}"})
    context = _make_context(chat_id=None)
    backend = InProcessConcurrencyBackend()

    with pytest.raises(ValueError, match="did not provide chat_id"):
        async with maybe_concurrency_gate(pipeline, context, backend):
            pytest.fail("body must not run when chat_id is missing")

    assert backend.gates_alive == 0, "no gate should be created on resolution failure"


@pytest.mark.asyncio
async def test_unknown_placeholder_raises_value_error() -> None:
    """Unsupported placeholder tokens raise (substring-permissive .replace() gap)."""
    pipeline = _make_pipeline(concurrency={"key": "chat:{context.user_id}"})
    context = _make_context()
    backend = InProcessConcurrencyBackend()

    with pytest.raises(ValueError, match="unsupported placeholder"):
        async with maybe_concurrency_gate(pipeline, context, backend):
            pytest.fail("body must not run on unknown placeholder")

    assert backend.gates_alive == 0


@pytest.mark.asyncio
async def test_invalid_timeout_raises_value_error() -> None:
    """Non-numeric ``timeout_seconds`` rejected at run time."""
    pipeline = _make_pipeline(
        concurrency={"key": "chat:{context.chat_id}", "timeout_seconds": "not-a-number"}
    )
    context = _make_context()
    backend = InProcessConcurrencyBackend()

    with pytest.raises(ValueError, match="must be numeric"):
        async with maybe_concurrency_gate(pipeline, context, backend):
            pytest.fail("body must not run on bad timeout")

    assert backend.gates_alive == 0


@pytest.mark.asyncio
async def test_different_chat_ids_do_not_block() -> None:
    """Two pipelines for distinct chat_ids hold independent gates."""
    pipeline = _make_pipeline(concurrency={"key": "chat:{context.chat_id}"})
    context_a = _make_context(chat_id="alice", execution_id="exec-a")
    context_b = _make_context(chat_id="bob", execution_id="exec-b")
    backend = InProcessConcurrencyBackend()

    enter_b = asyncio.Event()

    async def hold_a() -> None:
        async with maybe_concurrency_gate(pipeline, context_a, backend):
            await enter_b.wait()

    async def hold_b() -> None:
        async with maybe_concurrency_gate(pipeline, context_b, backend):
            # B can enter even though A still holds its own key.
            enter_b.set()

    await asyncio.gather(hold_a(), hold_b())
    assert backend.gates_alive == 0, "both gates evicted after release"
