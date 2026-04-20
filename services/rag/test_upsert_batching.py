"""ChromaDB upsert respects max_batch_size via create_batches."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.rag.rag_service.indexing import _upsert_chroma_chunk_batches


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
