"""Tests for embed diff gate, content-addressed chunk IDs, and corpus reindex sweep."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.rag.chunkers import Chunk
from services.rag.rag_service.indexing.embed import _run_embed_phase
from services.rag.rag_service.indexing.embed_diff import (
    compose_chunk_id,
    compute_chunk_hash,
    compute_path_key,
    compute_stale_ids,
    is_legacy_chunk_id,
    is_new_scheme_chunk_id,
    partition_embed_work,
)
from services.rag.scripts.rag_corpus_id_reindex import run_corpus_id_reindex


def _chunk(text: str) -> Chunk:
    return Chunk(text=text, metadata={})


def _legacy_id(prefix: str, index: int) -> str:
    return f"{prefix}-{index}"


def test_cross_file_collision_distinct_ids_t1() -> None:
    """Same text+index at two paths → distinct chunk IDs (path_key differs)."""
    text = "shared paragraph"
    index = 0
    chunk_hash = compute_chunk_hash(index, text)
    path_a = "/tmp/corpus/a/doc.md"
    path_b = "/tmp/corpus/b/doc.md"
    id_a = compose_chunk_id(compute_path_key(path_a), chunk_hash)
    id_b = compose_chunk_id(compute_path_key(path_b), chunk_hash)
    assert id_a != id_b
    assert is_new_scheme_chunk_id(id_a)
    assert is_new_scheme_chunk_id(id_b)


def test_rename_changes_path_key_t8() -> None:
    """Rename changes path_key → new IDs; old IDs become stale."""
    text = "stable"
    index = 0
    chunk_hash = compute_chunk_hash(index, text)
    old_path = "/tmp/corpus/old-name.md"
    new_path = "/tmp/corpus/new-name.md"
    old_id = compose_chunk_id(compute_path_key(old_path), chunk_hash)
    new_id = compose_chunk_id(compute_path_key(new_path), chunk_hash)
    assert old_id != new_id
    stale = compute_stale_ids([old_id], [new_id])
    assert stale == [old_id]


def test_legacy_vs_new_scheme_detection() -> None:
    assert is_legacy_chunk_id("abcd1234abcd1234-47")
    assert not is_legacy_chunk_id("abcd1234abcd1234-deadbeefcafebabe")
    assert is_new_scheme_chunk_id("abcd1234abcd1234-deadbeefcafebabe")


def test_partition_skips_id_in_existing_and_cache_hit_t4() -> None:
    path = "/tmp/doc.md"
    path_key = compute_path_key(path)
    texts = [f"chunk{i}" for i in range(48)]
    ids = [
        compose_chunk_id(path_key, compute_chunk_hash(i, texts[i]))
        for i in range(47)
    ]
    new_hash = compute_chunk_hash(47, texts[47])
    new_id = compose_chunk_id(path_key, new_hash)
    ids.append(new_id)
    existing = ids[:-1]
    cache_hits = [True] * 47 + [False]
    diff = partition_embed_work(
        ids=ids, existing_ids=existing, cache_hit_flags=cache_hits
    )
    assert diff.skipped_count == 47
    assert diff.processed_count == 1
    assert diff.processed_indices == [47]


def test_boundary_context_changed_not_skipped_t5() -> None:
    """Stable chunk_hash but cache miss (neighbor/context changed) → processed."""
    path = "/tmp/doc.md"
    path_key = compute_path_key(path)
    text = "boundary chunk"
    index = 46
    chunk_hash = compute_chunk_hash(index, text)
    chunk_id = compose_chunk_id(path_key, chunk_hash)
    diff = partition_embed_work(
        ids=[chunk_id],
        existing_ids=[chunk_id],
        cache_hit_flags=[False],
    )
    assert diff.processed_count == 1
    assert diff.skipped_count == 0


def test_stale_ids_from_full_set_skipped_not_deleted_t6() -> None:
    path = "/tmp/doc.md"
    path_key = compute_path_key(path)
    existing = [
        compose_chunk_id(path_key, compute_chunk_hash(i, f"c{i}")) for i in range(47)
    ]
    new_ids = list(existing)
    new_ids.append(compose_chunk_id(path_key, compute_chunk_hash(47, "new")))
    stale = compute_stale_ids(existing, new_ids)
    assert stale == []
    legacy_stale = compute_stale_ids(
        [_legacy_id("deadbeefdeadbeef", i) for i in range(47)], new_ids
    )
    assert len(legacy_stale) == 47


def test_append_47_to_48_stale_ids_empty_t3() -> None:
    path = "/tmp/p4prime.md"
    path_key = compute_path_key(path)
    old_texts = [f"line{i}" for i in range(47)]
    old_ids = [
        compose_chunk_id(path_key, compute_chunk_hash(i, old_texts[i]))
        for i in range(47)
    ]
    new_texts = old_texts + ["appended line"]
    new_ids = [
        compose_chunk_id(path_key, compute_chunk_hash(i, new_texts[i]))
        for i in range(48)
    ]
    stale = compute_stale_ids(old_ids, new_ids)
    assert stale == []
    cache_hits = [True] * 47 + [False]
    diff = partition_embed_work(
        ids=new_ids, existing_ids=old_ids, cache_hit_flags=cache_hits
    )
    assert diff.skipped_count == 47
    assert diff.processed_count == 1


@pytest.mark.asyncio
async def test_embed_phase_append_emits_diff_evaluated_and_subsets_embed() -> None:
    """Integration: embed phase skips 47/48 on cache hits; embed called once."""
    path = Path("/tmp/p4prime-embed.md")
    source = str(path.resolve())
    path_key = compute_path_key(source)
    old_texts = [f"line{i}" for i in range(47)]
    existing_ids = [
        compose_chunk_id(path_key, compute_chunk_hash(i, old_texts[i]))
        for i in range(47)
    ]
    chunks = [_chunk(t) for t in old_texts] + [_chunk("new tail")]
    config = MagicMock()
    config.contextualize_model = "test-model"
    config.contextualize_client_timeout_s = 30.0
    collection = MagicMock()
    chroma_client = MagicMock()
    chroma_client.get_max_batch_size.return_value = 5000
    event_bus = MagicMock()
    event_bus.publish_nowait = AsyncMock()
    embed_texts = [f"ctx\n\n{c.text}" for c in chunks]
    cache_hit_flags = [True] * 47 + [False]

    with (
        patch(
            "services.rag.rag_service.indexing.embed._run_contextualization_phase",
            new_callable=AsyncMock,
            return_value=(embed_texts, [], cache_hit_flags),
        ),
        patch(
            "services.rag.rag_service.indexing.embed.embed_chunks",
            new_callable=AsyncMock,
            return_value=[[0.1, 0.2]],
        ) as mock_embed,
        patch(
            "services.rag.rag_service.indexing.embed._upsert_chroma_chunk_batches",
            new_callable=AsyncMock,
        ),
    ):
        result = await _run_embed_phase(
            file_path=path,
            source=source,
            source_hash=hashlib.sha256(b"x").hexdigest(),
            chunks=chunks,
            existing_ids=existing_ids,
            existing_timestamps={},
            metadata_overrides=None,
            prop_index=None,
            collection=collection,
            chroma_client=chroma_client,
            event_bus=event_bus,
            config=config,
            correlation_id="op-1",
            operation="reindex",
        )

    assert result.stale_ids == []
    assert len(result.ids) == 48
    mock_embed.assert_awaited_once()
    assert mock_embed.await_args.args[0] == [embed_texts[47]]
    diff_events = [
        c.args[0]
        for c in event_bus.publish_nowait.await_args_list
        if c.args[0].signal == "rag.embed.diff.evaluated"
    ]
    assert len(diff_events) == 1
    assert diff_events[0].payload["skipped_chunks"] == 47
    assert diff_events[0].payload["processed_chunks"] == 1


@pytest.mark.asyncio
async def test_corpus_sweep_migrates_fixture_sources_t7() -> None:
    sources = ["/tmp/fixture/a.md", "/tmp/fixture/b.md"]
    indexed: list[str] = []

    async def _index(
        path: Path,
        _overrides: object,
        *,
        force: bool,
        emit_skip_event: bool,
        operation: str,
    ) -> object:
        assert force is True
        assert operation == "reindex"
        assert emit_skip_event is False
        indexed.append(str(path))
        return MagicMock()

    with patch.object(Path, "exists", return_value=True):
        result = await run_corpus_id_reindex(
            list_sources=lambda: list(sources),
            index_one=_index,
            batch_size=2,
            dry_run=False,
        )

    assert result.total == 2
    assert result.scheduled == 2
    assert result.succeeded == 2
    assert result.failed == 0
    assert set(indexed) == set(sources)


@pytest.mark.asyncio
async def test_corpus_sweep_dry_run_counts_without_index() -> None:
    result = await run_corpus_id_reindex(
        list_sources=lambda: ["/tmp/a.md", "/tmp/b.md"],
        index_one=AsyncMock(),
        dry_run=True,
    )
    assert result.scheduled == 2
    assert result.succeeded == 0
