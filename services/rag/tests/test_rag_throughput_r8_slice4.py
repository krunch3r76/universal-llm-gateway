"""RAG-side r8 slice 4 tests (4.24, 4.25, 4.28)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest

from services.rag.config._models import DEFAULT_QUERY_EMBED_REQUEST_TIMEOUT_S
from services.rag.contextualize import (
    CONTEXTUALIZE_GLOBAL_MAX,
    contextualize_chunks,
    contextualize_inflight_permits,
)
from services.rag.embeddings.constants import QUERY_EMBED_REQUEST_TIMEOUT_S
from services.rag.embeddings.cosine_similarity import cosine_similarity
from services.rag.property_index import PropertyIndex
from services.rag.search_scope.property_boost import apply_property_boost


@pytest.fixture(autouse=True)
def _reset_contextualize_budget() -> None:
    import services.rag.contextualize as contextualize_mod

    contextualize_mod._contextualize_budget_available = CONTEXTUALIZE_GLOBAL_MAX
    contextualize_mod._contextualize_inflight_permits = 0
    yield


def test_4_24_query_embed_timeout_default_not_rag_client_ceiling() -> None:
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
async def test_4_25_property_boost_batch_lookup() -> None:
    db_path = Path("/tmp") / f"prop-boost-{uuid4().hex}.sqlite"
    index = PropertyIndex(db_path=db_path)
    await index.start()
    try:
        await index.add("prop.name@@Gateway", "chunk-a", source="doc")
        ids, _chunks, _metas, dists, hits = await asyncio.wait_for(
            apply_property_boost(
                ids=["chunk-a", "chunk-b"],
                chunks=["a", "b"],
                metadatas=[{}, {}],
                distances=[0.5, 0.6],
                query="Gateway Stargate",
                property_index=index,
                boost_factor=0.5,
            ),
            timeout=2.0,
        )
    finally:
        await index.stop()
    assert hits == 1
    assert dists[0] == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_4_28_contextualize_global_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    assert CONTEXTUALIZE_GLOBAL_MAX == 8
    peak_permits = 0

    async def _fake_post(*args, **kwargs):
        nonlocal peak_permits
        peak_permits = max(peak_permits, contextualize_inflight_permits())
        n = len(kwargs.get("json", {}).get("pipeline_options", {}).get("chunks", []))
        payload = json.dumps([{"context": "ok"}] * max(1, n))

        class _Resp:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"choices": [{"message": {"content": payload}}]}

        await asyncio.sleep(0.03)
        return _Resp()

    import services.rag.contextualize as contextualize_mod

    monkeypatch.setattr(contextualize_mod, "_CLIENT", type("_C", (), {"post": _fake_post})())
    from services.rag.chunkers import Chunk

    worker_sem = asyncio.Semaphore(4)

    async def _one_file(file_idx: int) -> None:
        async with worker_sem:
            chunks = [Chunk(text=f"t{file_idx}-{i}", metadata={}) for i in range(8)]
            await contextualize_chunks(
                chunks,
                source=f"f{file_idx}",
                model="qwen3-5-9b-q8-0-131072",
            )

    await asyncio.gather(*[_one_file(i) for i in range(20)])
    assert peak_permits <= 8
    assert peak_permits >= 5
