from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.rag.directory_ops import index_directory_contents
from services.rag.indexing_helpers import migrate_chroma_source
from services.rag.models import IndexResult
from services.rag.property_index import IndexedSourceSnapshot
from services.rag.rag_service.indexing import _should_skip_cached_source
from services.rag.rag_service.indexing.embed_diff import (
    compose_chunk_id,
    compute_chunk_hash,
    compute_path_key,
    is_legacy_chunk_id,
)
from services.rag.rag_service.indexing.file_guards import _handle_unchanged_prefix_skip


def _cached_source() -> IndexedSourceSnapshot:
    return IndexedSourceSnapshot(
        source="/tmp/doc.md",
        mtime_ns=123,
        size_bytes=456,
        extraction_schema_version=7,
        extraction_model="rag-extraction",
        updated_at="2026-04-03T00:00:00Z",
    )


def test_should_skip_cached_source_for_normal_index() -> None:
    assert _should_skip_cached_source(
        force=False,
        operation="index",
        cached_source=_cached_source(),
        source_mtime_ns=123,
        source_size_bytes=456,
    )


def test_should_not_skip_cached_source_for_reindex() -> None:
    assert not _should_skip_cached_source(
        force=False,
        operation="reindex",
        cached_source=_cached_source(),
        source_mtime_ns=123,
        source_size_bytes=456,
    )


@pytest.mark.asyncio
async def test_index_directory_contents_passes_operation_to_indexer() -> None:
    calls: list[str | None] = []

    async def _index_file(
        path: Path,
        metadata_overrides: dict[str, str | int | float | bool] | None = None,
        *,
        force: bool = False,
        operation_id: str | None = None,
        operation: str | None = None,
    ) -> IndexResult:
        calls.append(operation)
        return IndexResult(
            indexed=1,
            deleted=0,
            unchanged=False,
            file=str(path),
        )

    totals = await index_directory_contents(
        file_paths=[Path("/tmp/doc.md")],
        index_file=_index_file,
        metadata_overrides=None,
        on_index_error=lambda _path, _exc: None,
        operation="reindex",
    )

    assert calls == ["reindex"]
    assert totals.indexed == 1
    assert totals.unchanged == 0
    assert totals.files == 1


@pytest.mark.asyncio
async def test_index_directory_contents_respects_max_concurrency() -> None:
    active = 0
    peak = 0

    async def _index_file(
        path: Path,
        metadata_overrides: dict[str, str | int | float | bool] | None = None,
        *,
        force: bool = False,
        operation_id: str | None = None,
        operation: str | None = None,
    ) -> IndexResult:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return IndexResult(
            indexed=1,
            deleted=0,
            unchanged=False,
            file=str(path),
        )

    totals = await index_directory_contents(
        file_paths=[Path(f"/tmp/doc-{i}.md") for i in range(6)],
        index_file=_index_file,
        metadata_overrides=None,
        on_index_error=lambda _path, _exc: None,
        operation="reindex",
        max_concurrency=2,
    )

    assert peak == 2
    assert totals.indexed == 6
    assert totals.files == 6


def test_migrate_chroma_source_queries_by_source_path() -> None:
    collection = MagicMock()
    collection.get.return_value = {
        "ids": ["id1"],
        "metadatas": [{"source": "/old/path.md", "source_hash": "abc"}],
    }
    n = migrate_chroma_source(
        collection, "abc", "/old/path.md", "/new/path.md"
    )
    assert n == 1
    collection.get.assert_called_once_with(
        where={"source": "/old/path.md"},
        include=["metadatas"],
    )


@pytest.mark.asyncio
async def test_unchanged_skip_on_source_hash_match_t2() -> None:
    """mtime/size may drift; identical bytes → source_hash match → skip."""
    source = "/tmp/unchanged.md"
    source_hash = "deadbeef" * 8
    prop_index = MagicMock()
    prop_index.get_indexed_source.return_value = IndexedSourceSnapshot(
        source=source,
        mtime_ns=100,
        size_bytes=200,
        extraction_schema_version=1,
        extraction_model="m",
        updated_at="2026-01-01T00:00:00Z",
        source_hash=source_hash,
    )
    prop_index.upsert_indexed_source = AsyncMock()
    prop_index.article_exists.return_value = True
    path_key = compute_path_key(source)
    existing_ids = [
        compose_chunk_id(path_key, compute_chunk_hash(i, f"c{i}"))
        for i in range(3)
    ]
    with patch(
        "services.rag.rag_service.indexing.file_guards._enqueue_for_extraction",
        new_callable=AsyncMock,
    ):
        result = await _handle_unchanged_prefix_skip(
            source=source,
            file_path=Path(source),
            existing_ids=existing_ids,
            prop_index=prop_index,
            source_stat=MagicMock(st_mtime_ns=999, st_size=999),
            source_hash=source_hash,
            schema_version=1,
            extraction_model="m",
            force=False,
            emit_skip_event=False,
            correlation_id="op",
            operation="index",
        )
    assert result is not None
    assert result.unchanged is True


@pytest.mark.asyncio
async def test_unchanged_skip_bypassed_for_legacy_ids() -> None:
    source = "/tmp/legacy.md"
    prop_index = MagicMock()
    prop_index.get_indexed_source.return_value = IndexedSourceSnapshot(
        source=source,
        mtime_ns=1,
        size_bytes=1,
        extraction_schema_version=1,
        extraction_model="m",
        updated_at="2026-01-01T00:00:00Z",
        source_hash="abc",
    )
    legacy_ids = [f"{'a' * 16}-{i}" for i in range(5)]
    assert all(is_legacy_chunk_id(i) for i in legacy_ids)
    result = await _handle_unchanged_prefix_skip(
        source=source,
        file_path=Path(source),
        existing_ids=legacy_ids,
        prop_index=prop_index,
        source_stat=MagicMock(),
        source_hash="abc",
        schema_version=1,
        extraction_model="m",
        force=False,
        emit_skip_event=False,
        correlation_id="op",
        operation="index",
    )
    assert result is None
