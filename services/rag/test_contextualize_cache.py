"""Unit tests for the contextualize prefix cache (pure planner + PropertyIndex I/O)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from services.rag.chunkers import Chunk
from services.rag.contextualize_cache import (
    CacheMissChunk,
    ContextCachePlan,
    StoredContextRow,
    build_context_cache_plan,
    build_stored_context_rows,
    merge_computed_contexts,
)
from services.rag.property_index import PropertyIndex


def _chunk(text: str) -> Chunk:
    return Chunk(text=text, metadata={})


# ---------------------------------------------------------------------------
# Pure planner/merger/row-builder tests (no I/O)
# ---------------------------------------------------------------------------


def test_build_plan_all_cached() -> None:
    chunks = [_chunk("a"), _chunk("b")]
    metadatas = [{"chunk_hash": "h1"}, {"chunk_hash": "h2"}]
    plan = build_context_cache_plan(
        chunks=chunks,
        metadatas=metadatas,
        cached_contexts={"h1": "P1", "h2": "P2"},
    )
    assert plan.contexts == ["P1", "P2"]
    assert plan.cache_misses == []
    assert plan.cache_hits == 2


def test_build_plan_all_missing() -> None:
    chunks = [_chunk("a"), _chunk("b")]
    metadatas = [{"chunk_hash": "h1"}, {"chunk_hash": "h2"}]
    plan = build_context_cache_plan(
        chunks=chunks, metadatas=metadatas, cached_contexts={}
    )
    assert plan.contexts == ["", ""]
    assert [m.chunk_hash for m in plan.cache_misses] == ["h1", "h2"]
    assert plan.cache_hits == 0


def test_build_plan_partial_preserves_order() -> None:
    chunks = [_chunk("a"), _chunk("b"), _chunk("c")]
    metadatas = [
        {"chunk_hash": "h1"},
        {"chunk_hash": "h2"},
        {"chunk_hash": "h3"},
    ]
    plan = build_context_cache_plan(
        chunks=chunks, metadatas=metadatas, cached_contexts={"h2": "P2"}
    )
    assert plan.contexts == ["", "P2", ""]
    assert [m.index for m in plan.cache_misses] == [0, 2]
    assert plan.cache_hits == 1


def test_build_plan_metadata_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        build_context_cache_plan(chunks=[_chunk("a")], metadatas=[], cached_contexts={})


def test_merge_preserves_order() -> None:
    plan = ContextCachePlan(
        contexts=["", "HIT", ""],
        cache_misses=[
            CacheMissChunk(index=0, chunk=_chunk("a"), chunk_hash="h1"),
            CacheMissChunk(index=2, chunk=_chunk("c"), chunk_hash="h3"),
        ],
        cache_hits=1,
    )
    merged = merge_computed_contexts(plan=plan, computed_prefixes=["P1", "P3"])
    assert merged == ["P1", "HIT", "P3"]
    assert plan.contexts == ["", "HIT", ""]  # unmutated


def test_merge_length_mismatch_raises() -> None:
    plan = ContextCachePlan(
        contexts=[""],
        cache_misses=[
            CacheMissChunk(index=0, chunk=_chunk("a"), chunk_hash="h1"),
        ],
        cache_hits=0,
    )
    with pytest.raises(ValueError):
        merge_computed_contexts(plan=plan, computed_prefixes=[])


def test_build_stored_context_rows_skips_empty_prefixes() -> None:
    """Empty prefixes from contextualize_chunks failure must NEVER enter cache."""
    plan = build_context_cache_plan(
        chunks=[_chunk("a"), _chunk("b")],
        metadatas=[{"chunk_hash": "h1"}, {"chunk_hash": "h2"}],
        cached_contexts={},
    )
    rows = build_stored_context_rows(plan=plan, computed_prefixes=["good prefix", ""])
    assert len(rows) == 1
    assert rows[0].chunk_hash == "h1"
    assert rows[0].context_prefix == "good prefix"


def test_build_stored_context_rows_skips_empty_chunk_hashes() -> None:
    plan = build_context_cache_plan(
        chunks=[_chunk("a")],
        metadatas=[{"chunk_hash": ""}],
        cached_contexts={},
    )
    rows = build_stored_context_rows(plan=plan, computed_prefixes=["P1"])
    assert rows == []


def test_build_stored_context_rows_length_mismatch_raises() -> None:
    plan = build_context_cache_plan(
        chunks=[_chunk("a")],
        metadatas=[{"chunk_hash": "h1"}],
        cached_contexts={},
    )
    with pytest.raises(ValueError):
        build_stored_context_rows(plan=plan, computed_prefixes=[])


# ---------------------------------------------------------------------------
# PropertyIndex round-trip tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def prop_index(tmp_path: Path) -> PropertyIndex:
    idx = PropertyIndex(db_path=tmp_path / "rag.db")
    await idx.start()
    try:
        yield idx
    finally:
        await idx.stop()


async def test_store_and_get_round_trip(prop_index: PropertyIndex) -> None:
    entries = [
        StoredContextRow(chunk_hash="h1", context_prefix="P1"),
        StoredContextRow(chunk_hash="h2", context_prefix="P2"),
    ]
    stored = await prop_index.store_cached_contexts(
        source_hash="S",
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=entries,
    )
    assert stored == 2

    got = prop_index.get_cached_contexts(
        source_hash="S",
        chunk_hashes=["h1", "h2", "h3"],
        contextualize_model="m",
        contextualize_schema_version="v1",
    )
    assert got == {"h1": "P1", "h2": "P2"}


async def test_store_idempotent_upsert(prop_index: PropertyIndex) -> None:
    await prop_index.store_cached_contexts(
        source_hash="S",
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=[StoredContextRow(chunk_hash="h1", context_prefix="old")],
    )
    await prop_index.store_cached_contexts(
        source_hash="S",
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=[StoredContextRow(chunk_hash="h1", context_prefix="new")],
    )
    assert prop_index.count_contextualized_chunks() == 1
    got = prop_index.get_cached_contexts(
        source_hash="S",
        chunk_hashes=["h1"],
        contextualize_model="m",
        contextualize_schema_version="v1",
    )
    assert got == {"h1": "new"}


async def test_store_rejects_empty_prefix_via_check_constraint(
    prop_index: PropertyIndex,
) -> None:
    # Storage-layer backstop: even if callers skip the planner filter,
    # CHECK(context_prefix <> '') rejects empty-prefix rows.
    conn = prop_index._ensure_conn()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO contextualized_chunks ("
            "  source_hash, chunk_hash, contextualize_model,"
            "  contextualize_schema_version, context_prefix"
            ") VALUES (?, ?, ?, ?, ?)",
            ("S", "h1", "m", "v1", ""),
        )


async def test_get_batched_over_900_hashes(prop_index: PropertyIndex) -> None:
    # Store 1000 rows, ensure batched IN() queries stitch results back together.
    entries = [
        StoredContextRow(chunk_hash=f"h{i}", context_prefix=f"P{i}")
        for i in range(1000)
    ]
    await prop_index.store_cached_contexts(
        source_hash="S",
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=entries,
    )
    got = prop_index.get_cached_contexts(
        source_hash="S",
        chunk_hashes=[f"h{i}" for i in range(1000)],
        contextualize_model="m",
        contextualize_schema_version="v1",
    )
    assert len(got) == 1000


async def test_different_model_bypasses(prop_index: PropertyIndex) -> None:
    await prop_index.store_cached_contexts(
        source_hash="S",
        contextualize_model="m1",
        contextualize_schema_version="v1",
        entries=[StoredContextRow(chunk_hash="h1", context_prefix="P1")],
    )
    got = prop_index.get_cached_contexts(
        source_hash="S",
        chunk_hashes=["h1"],
        contextualize_model="m2",
        contextualize_schema_version="v1",
    )
    assert got == {}


async def test_different_schema_version_bypasses(prop_index: PropertyIndex) -> None:
    await prop_index.store_cached_contexts(
        source_hash="S",
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=[StoredContextRow(chunk_hash="h1", context_prefix="P1")],
    )
    got = prop_index.get_cached_contexts(
        source_hash="S",
        chunk_hashes=["h1"],
        contextualize_model="m",
        contextualize_schema_version="v2",
    )
    assert got == {}


async def test_delete_by_source_hash_removes_rows(prop_index: PropertyIndex) -> None:
    await prop_index.store_cached_contexts(
        source_hash="S",
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=[StoredContextRow(chunk_hash="h1", context_prefix="P1")],
    )
    removed = await prop_index.delete_cached_contexts_for_source_hash("S")
    assert removed == 1
    assert prop_index.count_contextualized_chunks() == 0


async def test_garbage_collect_removes_orphans(prop_index: PropertyIndex) -> None:
    await prop_index.upsert_indexed_source(
        source="/a",
        mtime_ns=0,
        size_bytes=0,
        extraction_schema_version=1,
        extraction_model="m",
        source_hash="LIVE",
    )
    await prop_index.store_cached_contexts(
        source_hash="LIVE",
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=[StoredContextRow(chunk_hash="h1", context_prefix="P1")],
    )
    await prop_index.store_cached_contexts(
        source_hash="ORPHAN",
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=[StoredContextRow(chunk_hash="h2", context_prefix="P2")],
    )
    deleted = await prop_index.garbage_collect_contextualized_chunks()
    assert deleted == 1
    assert prop_index.count_contextualized_chunks() == 1


async def test_get_cached_contexts_bypasses_when_source_hash_empty(
    prop_index: PropertyIndex,
) -> None:
    got = prop_index.get_cached_contexts(
        source_hash="",
        chunk_hashes=["h1"],
        contextualize_model="m",
        contextualize_schema_version="v1",
    )
    assert got == {}


async def test_store_cached_contexts_bypasses_when_source_hash_empty(
    prop_index: PropertyIndex,
) -> None:
    stored = await prop_index.store_cached_contexts(
        source_hash="",
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=[StoredContextRow(chunk_hash="h1", context_prefix="P1")],
    )
    assert stored == 0
    assert prop_index.count_contextualized_chunks() == 0
