from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from services.rag.directory_ops import index_directory_contents
from services.rag.models import IndexResult
from services.rag.property_index import IndexedSourceSnapshot
from services.rag.rag_service.indexing import _should_skip_cached_source


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
        schema_version=7,
        extraction_model="rag-extraction",
        has_retriable_failures=False,
    )


def test_should_not_skip_cached_source_for_reindex() -> None:
    assert not _should_skip_cached_source(
        force=False,
        operation="reindex",
        cached_source=_cached_source(),
        source_mtime_ns=123,
        source_size_bytes=456,
        schema_version=7,
        extraction_model="rag-extraction",
        has_retriable_failures=False,
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
