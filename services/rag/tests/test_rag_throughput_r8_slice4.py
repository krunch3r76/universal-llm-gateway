"""RAG-side r8 slice 4 tests (4.24, 4.25, 4.28)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from services.rag.config._models import DEFAULT_QUERY_EMBED_REQUEST_TIMEOUT_S
from services.rag.contextualize import CONTEXTUALIZE_GLOBAL_MAX, contextualize_chunks
from services.rag.embeddings.constants import QUERY_EMBED_REQUEST_TIMEOUT_S
from services.rag.embeddings.cosine_similarity import cosine_similarity
from services.rag.search_scope.property_boost import apply_property_boost


def test_4_23_query_embed_timeout_default_not_rag_client_ceiling() -> None:
    assert DEFAULT_QUERY_EMBED_REQUEST_TIMEOUT_S == 10.0
    assert QUERY_EMBED_REQUEST_TIMEOUT_S == 10.0
    assert QUERY_EMBED_REQUEST_TIMEOUT_S < 120.0


def test_4_24_cosine_helper_identity() -> None:
    vec = [1.0, 0.0, 0.0]
    assert cosine_similarity(vec, vec) == pytest.approx(1.0, abs=1e-6)


def test_4_24_fixture_has_twenty_texts() -> None:
    path = Path(__file__).parent / "fixtures" / "embedding_cosine_20_texts.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["texts"]) == 20


@pytest.mark.asyncio
async def test_4_25_property_boost_batch_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    class _Index:
        async def run_sqlite_async(self, fn):
            return fn()

        def lookup_keys_batch(self, keys: list[str]) -> set[str]:
            calls.append(keys)
            return {"chunk-a"} if keys else set()

    ids, chunks, metas, dists, hits = await apply_property_boost(
        ids=["chunk-a", "chunk-b"],
        chunks=["a", "b"],
        metadatas=[{}, {}],
        distances=[0.5, 0.6],
        query="Gateway Stargate",
        property_index=_Index(),  # type: ignore[arg-type]
        boost_factor=0.5,
    )
    assert len(calls) == 1
    assert hits == 1
    assert dists[0] == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_4_28_contextualize_global_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    assert CONTEXTUALIZE_GLOBAL_MAX == 8
    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def _fake_post(*args, **kwargs):
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1

        class _Resp:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "choices": [{"message": {"content": '[{"context": "ok"}]'}}],
                }

        return _Resp()

    monkeypatch.setattr("services.rag.contextualize._CLIENT.post", _fake_post)
    from services.rag.chunkers import Chunk

    chunks = [Chunk(text=f"t{i}", metadata={}) for i in range(6)]
    tasks = [
        contextualize_chunks(chunks[:1], source=f"f{i}", model="qwen3-5-9b-q8-0-131072")
        for i in range(6)
    ]
    await asyncio.gather(*tasks)
    assert max_in_flight <= 8
