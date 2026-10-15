"""ChromaDB upsert respects max_batch_size via create_batches."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.rag.rag_service.api import health
from services.rag.rag_service.indexing.chroma import _upsert_chroma_chunk_batches
from services.rag.rag_service.loop_instrumentation import (
    _loop_drift_monitor,
    start_loop_instrumentation,
    stop_loop_instrumentation,
)


class _FakeChroma:
    def __init__(self, max_batch_size: int) -> None:
        self._max = max_batch_size

    def get_max_batch_size(self) -> int:
        return self._max


@pytest.mark.asyncio
async def test_upsert_splits_into_two_batches_when_over_max() -> None:
    # total = max_bs + 100 ⇒ two batches iff max_bs >= 100 (ceil division).
    max_bs = 1000
    total = max_bs + 100
    chroma = _FakeChroma(max_bs)
    collection = MagicMock()
    event_bus = None

    ids = [f"id{i}" for i in range(total)]
    embeddings = [[0.0, 1.0] for _ in range(total)]
    texts = [f"t{i}" for i in range(total)]
    metadatas = [{"i": i} for i in range(total)]

    await _upsert_chroma_chunk_batches(
        chroma_client=chroma,
        collection=collection,
        event_bus=event_bus,
        source="/tmp/large.md",
        correlation_id="op-1",
        operation="index",
        ids=ids,
        embeddings=embeddings,
        texts=texts,
        metadatas=metadatas,
    )

    assert collection.upsert.call_count == 2
    first = collection.upsert.call_args_list[0].kwargs
    second = collection.upsert.call_args_list[1].kwargs
    assert len(first["ids"]) == max_bs
    assert len(second["ids"]) == total - max_bs


@pytest.mark.asyncio
async def test_upsert_emits_started_completed_per_batch() -> None:
    max_bs = 1000
    total = max_bs + 100
    chroma = _FakeChroma(max_bs)
    collection = MagicMock()
    event_bus = MagicMock()
    event_bus.publish_nowait = AsyncMock()

    ids = [f"id{i}" for i in range(total)]
    embeddings = [[0.0] for _ in range(total)]
    texts = [f"t{i}" for i in range(total)]
    metadatas = [{"i": i} for i in range(total)]

    await _upsert_chroma_chunk_batches(
        chroma_client=chroma,
        collection=collection,
        event_bus=event_bus,
        source="/tmp/large.md",
        correlation_id="op-1",
        operation="reindex",
        ids=ids,
        embeddings=embeddings,
        texts=texts,
        metadatas=metadatas,
    )

    assert event_bus.publish_nowait.await_count == 4
    events = [c.args[0] for c in event_bus.publish_nowait.call_args_list]
    assert all(e.signal.startswith("rag.chroma.upsert.") for e in events)
    assert events[0].signal == "rag.chroma.upsert.started"
    assert events[0].payload["batch_index"] == 0
    assert events[0].payload["batch_total"] == 2
    assert events[3].signal == "rag.chroma.upsert.completed"
    assert events[3].payload["batch_index"] == 1
    assert events[3].payload["batch_total"] == 2


@pytest.mark.asyncio
async def test_loop_drift_and_gil_gap_are_distinct_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failure mode 11: loop_drift_ms and gil_gap_ms are separate log fields."""
    caplog.set_level(logging.INFO)
    stop = asyncio.Event()
    task = asyncio.create_task(_loop_drift_monitor(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.gather(task, return_exceptions=True)
    drift_lines = [r.message for r in caplog.records if "loop_drift_ms=" in r.message]
    assert drift_lines
    assert not any("loop_lag_ms" in r.message for r in caplog.records)
    caplog.clear()
    with patch(
        "services.rag.rag_service.loop_instrumentation.time.sleep"
    ) as mock_sleep:
        mock_sleep.side_effect = lambda _s: None
        start_loop_instrumentation()
        await asyncio.sleep(0.05)
        await stop_loop_instrumentation()
    gil_lines = [r.message for r in caplog.records if "gil_gap_ms=" in r.message]
    assert any("GIL" in line for line in gil_lines) or caplog.records


@pytest.mark.asyncio
async def test_health_logs_handler_ms_and_count_wait_ms(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failure mode 12: health_handler_ms covers queue breakdown; count_wait_ms does not."""
    from services.rag.rag_service import state

    caplog.set_level(logging.INFO)
    slow = MagicMock()
    slow.total = slow.ready = slow.in_flight = 0
    slow.cooling_off = slow.capacity_blocked = slow.exhausted = 0

    def slow_breakdown() -> MagicMock:
        import time

        time.sleep(0.02)
        return slow

    collection = MagicMock()
    collection.count.return_value = 42
    activation = state.DependencyActivationState()
    activation.phase = "ready"
    with (
        patch.object(state, "_collection", collection),
        patch.object(
            state,
            "_property_index",
            MagicMock(get_extraction_queue_breakdown=slow_breakdown),
        ),
        patch.object(state, "_dependency_activation", activation),
        patch.object(state, "_watcher_manager", None),
        patch(
            "services.rag.rag_service.api.get_embed_model_id", return_value="test-model"
        ),
        patch(
            "services.rag.rag_service.api.resolve_code_version", return_value="test-sha"
        ),
    ):
        result = health()
    assert result["collection_count"] == 42
    messages = " ".join(r.message for r in caplog.records)
    assert "count_wait_ms=" in messages
    assert "health_handler_ms=" in messages
    assert "loop_lag_ms" not in messages
