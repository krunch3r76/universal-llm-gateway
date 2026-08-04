"""Unit tests for the contextualize prefix cache (pure planner + PropertyIndex I/O)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio

from services.rag.chunkers import Chunk
from services.rag.contextualize import (
    ContextualizationPhaseError,
    compute_neighbor_digest,
)
from services.rag.contextualize_cache import (
    CacheMissChunk,
    ContextCachePlan,
    StoredContextRow,
    build_context_cache_plan,
    build_stored_context_rows,
    merge_computed_contexts,
    resolve_source_identity,
)
from services.rag.indexing_failure_classifier import classify_indexing_failure
from services.rag.property_index import PropertyIndex
from services.rag.watcher_manager import WatcherManager


def _chunk(text: str) -> Chunk:
    return Chunk(text=text, metadata={})


def _digest(chunks: list[Chunk], idx: int) -> str:
    return compute_neighbor_digest(chunks, idx)


def _row(
    chunk_hash: str,
    prefix: str,
    *,
    chunks: list[Chunk] | None = None,
    idx: int = 0,
) -> StoredContextRow:
    chunk_list = chunks or [_chunk("solo")]
    return StoredContextRow(
        chunk_hash=chunk_hash,
        context_prefix=prefix,
        neighbor_digest=_digest(chunk_list, idx),
    )


def _source_id(path: str) -> str:
    return resolve_source_identity(path)


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
    chunks = [_chunk("a"), _chunk("b"), _chunk("c")]
    plan = ContextCachePlan(
        contexts=["", "HIT", ""],
        cache_misses=[
            CacheMissChunk(
                index=0, chunk=chunks[0], chunk_hash="h1", neighbor_digest=_digest(chunks, 0)
            ),
            CacheMissChunk(
                index=2, chunk=chunks[2], chunk_hash="h3", neighbor_digest=_digest(chunks, 2)
            ),
        ],
        cache_hits=1,
    )
    merged = merge_computed_contexts(plan=plan, computed_prefixes=["P1", "P3"])
    assert merged == ["P1", "HIT", "P3"]
    assert plan.contexts == ["", "HIT", ""]  # unmutated


def test_merge_length_mismatch_raises() -> None:
    chunks = [_chunk("a")]
    plan = ContextCachePlan(
        contexts=[""],
        cache_misses=[
            CacheMissChunk(
                index=0, chunk=chunks[0], chunk_hash="h1", neighbor_digest=_digest(chunks, 0)
            ),
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


def test_append_delta_hit_miss_boundary_ac_b1() -> None:
    """T1: append Δ chunks → hits ≈ N−Δ−1, misses ≈ Δ+1 (boundary chunk)."""
    n = 48
    delta = 2
    chunks = [_chunk(f"c{i}") for i in range(n)]
    metadatas = [{"chunk_hash": f"h{i}"} for i in range(n)]
    prefix_rows = {
        f"h{i}": f"P{i}" for i in range(n - delta - 1)
    }
    plan = build_context_cache_plan(
        chunks=chunks, metadatas=metadatas, cached_contexts=prefix_rows
    )
    assert plan.cache_hits == n - delta - 1
    assert plan.cache_misses_count == delta + 1


def test_boundary_neighbor_digest_changes_on_append() -> None:
    chunks_before = [_chunk("a"), _chunk("boundary")]
    chunks_after = [_chunk("a"), _chunk("boundary"), _chunk("new")]
    assert _digest(chunks_before, 1) != _digest(chunks_after, 1)


# ---------------------------------------------------------------------------
# PropertyIndex round-trip tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def prop_index(tmp_path: Path) -> PropertyIndex:
    idx = PropertyIndex(db_path=tmp_path / "rag.db")
    await idx.start()
    try:
        yield idx
    finally:
        await idx.stop()


@pytest.mark.asyncio
async def test_store_and_get_round_trip(prop_index: PropertyIndex) -> None:
    source = "/tmp/rag-cache-round-trip.md"
    chunks = [_chunk("a"), _chunk("b")]
    entries = [
        _row("h1", "P1", chunks=chunks, idx=0),
        _row("h2", "P2", chunks=chunks, idx=1),
    ]
    stored = await prop_index.store_cached_contexts(
        source_identity=_source_id(source),
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=entries,
    )
    assert stored == 2

    got = prop_index.get_cached_contexts(
        source_identity=_source_id(source),
        chunk_hashes=["h1", "h2", "h3"],
        neighbor_digests={"h1": _digest(chunks, 0), "h2": _digest(chunks, 1)},
        contextualize_model="m",
        contextualize_schema_version="v1",
    )
    assert got == {"h1": "P1", "h2": "P2"}


@pytest.mark.asyncio
async def test_store_idempotent_upsert(prop_index: PropertyIndex) -> None:
    source = "/tmp/rag-cache-upsert.md"
    chunks = [_chunk("a")]
    identity = _source_id(source)
    await prop_index.store_cached_contexts(
        source_identity=identity,
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=[_row("h1", "old", chunks=chunks, idx=0)],
    )
    await prop_index.store_cached_contexts(
        source_identity=identity,
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=[_row("h1", "new", chunks=chunks, idx=0)],
    )
    assert prop_index.count_contextualized_chunks() == 1
    got = prop_index.get_cached_contexts(
        source_identity=identity,
        chunk_hashes=["h1"],
        neighbor_digests={"h1": _digest(chunks, 0)},
        contextualize_model="m",
        contextualize_schema_version="v1",
    )
    assert got == {"h1": "new"}


@pytest.mark.asyncio
async def test_store_rejects_empty_prefix_via_check_constraint(
    prop_index: PropertyIndex,
) -> None:
    conn = prop_index._ensure_conn()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO contextualized_chunks_g1 ("
            "  source_identity, chunk_hash, neighbor_digest, contextualize_model,"
            "  contextualize_schema_version, context_prefix"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            ("S", "h1", "d" * 64, "m", "v1", ""),
        )


@pytest.mark.asyncio
async def test_get_batched_over_900_hashes(prop_index: PropertyIndex) -> None:
    source = "/tmp/rag-cache-batch.md"
    identity = _source_id(source)
    solo = [_chunk("solo")]
    digest = _digest(solo, 0)
    entries = [
        _row(f"h{i}", f"P{i}", chunks=solo, idx=0) for i in range(1000)
    ]
    await prop_index.store_cached_contexts(
        source_identity=identity,
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=entries,
    )
    got = prop_index.get_cached_contexts(
        source_identity=identity,
        chunk_hashes=[f"h{i}" for i in range(1000)],
        neighbor_digests={f"h{i}": digest for i in range(1000)},
        contextualize_model="m",
        contextualize_schema_version="v1",
    )
    assert len(got) == 1000


@pytest.mark.asyncio
async def test_different_model_bypasses(prop_index: PropertyIndex) -> None:
    source = "/tmp/rag-cache-model.md"
    chunks = [_chunk("a")]
    identity = _source_id(source)
    await prop_index.store_cached_contexts(
        source_identity=identity,
        contextualize_model="m1",
        contextualize_schema_version="v1",
        entries=[_row("h1", "P1", chunks=chunks, idx=0)],
    )
    got = prop_index.get_cached_contexts(
        source_identity=identity,
        chunk_hashes=["h1"],
        neighbor_digests={"h1": _digest(chunks, 0)},
        contextualize_model="m2",
        contextualize_schema_version="v1",
    )
    assert got == {}


@pytest.mark.asyncio
async def test_different_schema_version_bypasses(prop_index: PropertyIndex) -> None:
    source = "/tmp/rag-cache-schema.md"
    chunks = [_chunk("a")]
    identity = _source_id(source)
    await prop_index.store_cached_contexts(
        source_identity=identity,
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=[_row("h1", "P1", chunks=chunks, idx=0)],
    )
    got = prop_index.get_cached_contexts(
        source_identity=identity,
        chunk_hashes=["h1"],
        neighbor_digests={"h1": _digest(chunks, 0)},
        contextualize_model="m",
        contextualize_schema_version="v2",
    )
    assert got == {}


@pytest.mark.asyncio
async def test_delete_by_source_identity_removes_rows(prop_index: PropertyIndex) -> None:
    source = "/tmp/rag-cache-delete.md"
    chunks = [_chunk("a")]
    identity = _source_id(source)
    await prop_index.store_cached_contexts(
        source_identity=identity,
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=[_row("h1", "P1", chunks=chunks, idx=0)],
    )
    removed = await prop_index.delete_cached_contexts_for_source_identity(identity)
    assert removed == 1
    assert prop_index.count_contextualized_chunks() == 0


@pytest.mark.asyncio
async def test_garbage_collect_removes_orphans_by_source_identity(
    prop_index: PropertyIndex,
) -> None:
    live_source = "/a/live.md"
    live_identity = _source_id(live_source)
    orphan_identity = _source_id("/orphan/other.md")
    chunks = [_chunk("a")]
    await prop_index.upsert_indexed_source(
        source=live_source,
        mtime_ns=0,
        size_bytes=0,
        extraction_schema_version=1,
        extraction_model="m",
        source_hash="LIVE",
    )
    await prop_index.store_cached_contexts(
        source_identity=live_identity,
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=[_row("h1", "P1", chunks=chunks, idx=0)],
    )
    await prop_index.store_cached_contexts(
        source_identity=orphan_identity,
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=[_row("h2", "P2", chunks=chunks, idx=0)],
    )
    deleted = await prop_index.garbage_collect_contextualized_chunks()
    assert deleted == 1
    assert prop_index.count_contextualized_chunks() == 1


@pytest.mark.asyncio
async def test_gc_survival_after_indexed_source_hash_change_ac_b1_t2(
    prop_index: PropertyIndex,
) -> None:
    """T2: prefix rows survive GC when source_identity remains in indexed_sources."""
    source = "/journal/hot.md"
    identity = _source_id(source)
    chunks = [_chunk("prefix")]
    await prop_index.upsert_indexed_source(
        source=source,
        mtime_ns=1,
        size_bytes=10,
        extraction_schema_version=1,
        extraction_model="m",
        source_hash="NEW_HASH_AFTER_APPEND",
    )
    await prop_index.store_cached_contexts(
        source_identity=identity,
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=[_row("h1", "P1", chunks=chunks, idx=0)],
    )
    deleted = await prop_index.garbage_collect_contextualized_chunks()
    assert deleted == 0
    got = prop_index.get_cached_contexts(
        source_identity=identity,
        chunk_hashes=["h1"],
        neighbor_digests={"h1": _digest(chunks, 0)},
        contextualize_model="m",
        contextualize_schema_version="v1",
    )
    assert got == {"h1": "P1"}


@pytest.mark.asyncio
async def test_dual_read_legacy_insert_fixture(prop_index: PropertyIndex) -> None:
    """T3: legacy V10 row is visible via dual-read fallback."""
    source = "/tmp/legacy-dual-read.md"
    chunks = [_chunk("stable")]
    conn = prop_index._ensure_conn()
    conn.execute(
        "INSERT INTO contextualized_chunks ("
        "  source_hash, chunk_hash, contextualize_model,"
        "  contextualize_schema_version, context_prefix"
        ") VALUES (?, ?, ?, ?, ?)",
        ("LEGACY_HASH", "h1", "m", "v1", "LEGACY_PREFIX"),
    )
    conn.commit()
    got = prop_index.get_cached_contexts(
        source_identity=_source_id(source),
        source_hash="LEGACY_HASH",
        chunk_hashes=["h1"],
        neighbor_digests={"h1": _digest(chunks, 0)},
        contextualize_model="m",
        contextualize_schema_version="v1",
    )
    assert got == {"h1": "LEGACY_PREFIX"}


@pytest.mark.asyncio
async def test_get_cached_contexts_bypasses_when_source_identity_empty(
    prop_index: PropertyIndex,
) -> None:
    got = prop_index.get_cached_contexts(
        source_identity="",
        chunk_hashes=["h1"],
        contextualize_model="m",
        contextualize_schema_version="v1",
    )
    assert got == {}


@pytest.mark.asyncio
async def test_store_cached_contexts_bypasses_when_source_identity_empty(
    prop_index: PropertyIndex,
) -> None:
    stored = await prop_index.store_cached_contexts(
        source_identity="",
        contextualize_model="m",
        contextualize_schema_version="v1",
        entries=[_row("h1", "P1")],
    )
    assert stored == 0
    assert prop_index.count_contextualized_chunks() == 0


@pytest.mark.asyncio
async def test_get_cached_contexts_bypasses_when_source_hash_empty_legacy(
    prop_index: PropertyIndex,
) -> None:
    got = prop_index.get_cached_contexts(
        source_hash="",
        chunk_hashes=["h1"],
        contextualize_model="m",
        contextualize_schema_version="v1",
    )
    assert got == {}


def test_contextualization_phase_error_chained_404_classifies_permanent() -> None:
    req = httpx.Request("GET", "http://test")
    resp = httpx.Response(status_code=404, request=req)
    http_exc = httpx.HTTPStatusError("missing", request=req, response=resp)
    phase_exc = ContextualizationPhaseError(
        "all failed",
        first_failure_exc=http_exc,
    )
    category, reason = classify_indexing_failure(phase_exc, chunk_count=0)
    assert category == "permanent"
    assert reason == "http_client_error"


def test_reindex_debounce_ms_is_2000_ac_b3() -> None:
    """T4 guard: watcher registration keeps 2000 ms debounce for write bursts."""
    from services.rag.watcher_manager import registration as reg

    source = Path(reg.__file__).read_text(encoding="utf-8")
    assert "debounce_ms=2000" in source


@pytest.mark.asyncio
async def test_transient_backoff_skips_inside_window_ac_b4_t8(tmp_path: Path) -> None:
    source_path = tmp_path / "doc.md"
    source_path.write_text("body", encoding="utf-8")
    prop_index = MagicMock()
    prop_index.get_indexing_failure.return_value = type(
        "Failure",
        (),
        {
            "failure_category": "transient",
            "failure_reason": "http_503",
            "attempt_count": 2,
            "source_mtime_ns": source_path.stat().st_mtime_ns,
            "source_size_bytes": source_path.stat().st_size,
            "last_failed_at": "2099-01-01T00:00:00",
        },
    )()
    emitted: list[object] = []
    wm = WatcherManager(index_fn=AsyncMock(), property_index=prop_index)
    wm._emit = AsyncMock(side_effect=lambda e: emitted.append(e))

    allowed = await wm._should_attempt(source_path)
    assert allowed is False
    assert any(e.signal == "rag.file.indexing.failure.skipped" for e in emitted)


@pytest.mark.asyncio
async def test_append_miss_count_matches_delta_ac_b5_t9() -> None:
    """T9: miss count on append reindex ≈ Δ (+ boundary), not N."""
    n = 20
    delta = 3
    chunks = [_chunk(f"x{i}") for i in range(n)]
    metadatas = [{"chunk_hash": f"h{i}"} for i in range(n)]
    cached = {f"h{i}": f"P{i}" for i in range(n - delta - 1)}
    plan = build_context_cache_plan(chunks=chunks, metadatas=metadatas, cached_contexts=cached)
    assert plan.cache_misses_count == delta + 1
    assert plan.cache_misses_count < n
