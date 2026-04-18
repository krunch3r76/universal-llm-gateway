"""File-level indexing failure tracking (Phase 2 of rag-reconcile-loop-fix)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from services.rag.property_index import PropertyIndex
from services.rag.rag_service.indexing import _classify_indexing_failure


def test_classifier_permanent_exceeds_batch_size() -> None:
    exc = ValueError("Batch contains X which exceeds max batch size of 1000")
    assert _classify_indexing_failure(exc, chunk_count=10) == (
        "permanent",
        "exceeds_chroma_max_batch_size",
    )


def test_classifier_permanent_not_in_catalog() -> None:
    exc = RuntimeError("model x NOT_IN_CATALOG structural failure")
    assert _classify_indexing_failure(exc, chunk_count=1) == (
        "permanent",
        "contextualize_model_not_in_catalog",
    )


def test_classifier_transient_probe_failed() -> None:
    exc = RuntimeError("PROBE_FAILED: stargate unreachable")
    assert _classify_indexing_failure(exc, chunk_count=1) == (
        "transient",
        "contextualize_probe_failed",
    )


def test_classifier_transient_unclassified_default() -> None:
    exc = RuntimeError("something weird happened")
    assert _classify_indexing_failure(exc, chunk_count=0) == (
        "transient",
        "unclassified",
    )


def test_classifier_permanent_permission() -> None:
    exc = PermissionError("denied")
    cat, _ = _classify_indexing_failure(exc, chunk_count=0)
    assert cat == "permanent"


def test_classifier_transient_timeout() -> None:
    exc = TimeoutError()
    cat, reason = _classify_indexing_failure(exc, chunk_count=0)
    assert (cat, reason) == ("transient", "timeout")


async def _make_index(tmp_path: Path) -> PropertyIndex:
    idx = PropertyIndex(db_path=tmp_path / "rag_metadata.db")
    await idx.start()
    return idx


@pytest.mark.asyncio
async def test_record_preserves_first_failed_at(tmp_path: Path) -> None:
    prop_index = await _make_index(tmp_path)
    src = "/fake/src.md"
    c1 = await prop_index.record_indexing_failure(
        source=src,
        failure_category="transient",
        failure_reason="timeout",
        error_message="boom",
        error_type="TimeoutError",
        source_hash="h",
        source_size_bytes=1,
        source_mtime_ns=1,
    )
    assert c1 == 1
    row1 = prop_index.get_indexing_failure(src)
    assert row1 is not None
    first = row1.first_failed_at
    time.sleep(1.1)
    c2 = await prop_index.record_indexing_failure(
        source=src,
        failure_category="permanent",
        failure_reason="unsupported_file_type",
        error_message="boom2",
        error_type="UnsupportedFileError",
        source_hash="h",
        source_size_bytes=1,
        source_mtime_ns=1,
    )
    assert c2 == 2
    row2 = prop_index.get_indexing_failure(src)
    assert row2 is not None
    assert row2.first_failed_at == first
    assert row2.failure_category == "permanent"
    assert row2.attempt_count == 2


@pytest.mark.asyncio
async def test_clear_returns_false_when_absent(tmp_path: Path) -> None:
    prop_index = await _make_index(tmp_path)
    assert await prop_index.clear_indexing_failure("/nonexistent") is False


@pytest.mark.asyncio
async def test_clear_returns_true_when_present(tmp_path: Path) -> None:
    prop_index = await _make_index(tmp_path)
    src = "/a"
    await prop_index.record_indexing_failure(
        source=src,
        failure_category="transient",
        failure_reason="timeout",
        error_message="x",
        error_type="T",
        source_hash=None,
        source_size_bytes=None,
        source_mtime_ns=None,
    )
    assert await prop_index.clear_indexing_failure(src) is True
    assert await prop_index.clear_indexing_failure(src) is False


@pytest.mark.asyncio
async def test_list_and_counts_filter(tmp_path: Path) -> None:
    prop_index = await _make_index(tmp_path)
    for i, cat in enumerate(["permanent", "transient", "permanent"]):
        await prop_index.record_indexing_failure(
            source=f"/s{i}",
            failure_category=cat,
            failure_reason="r",
            error_message="e",
            error_type="E",
            source_hash=None,
            source_size_bytes=None,
            source_mtime_ns=None,
        )
    perm = prop_index.list_indexing_failures(category="permanent")
    trans = prop_index.list_indexing_failures(category="transient")
    all_rows = prop_index.list_indexing_failures()
    assert len(perm) == 2
    assert len(trans) == 1
    assert len(all_rows) == 3
    permanent_count, transient_count = prop_index.get_indexing_failure_counts()
    assert (permanent_count, transient_count) == (2, 1)


@pytest.mark.asyncio
async def test_invalidation_by_content_change(tmp_path: Path) -> None:
    prop_index = await _make_index(tmp_path)
    src = "/b"
    await prop_index.record_indexing_failure(
        source=src,
        failure_category="permanent",
        failure_reason="x",
        error_message="e",
        error_type="E",
        source_hash="h",
        source_size_bytes=100,
        source_mtime_ns=111,
    )
    assert prop_index.is_indexing_failure_invalidated_by_content(src, 111, 100) is False
    assert prop_index.is_indexing_failure_invalidated_by_content(src, 222, 100) is True
    assert prop_index.is_indexing_failure_invalidated_by_content(src, 111, 999) is True
    assert (
        prop_index.is_indexing_failure_invalidated_by_content("/missing", 1, 1) is False
    )


@pytest.mark.asyncio
async def test_iso_last_failed_at_parseable(tmp_path: Path) -> None:
    prop_index = await _make_index(tmp_path)
    await prop_index.record_indexing_failure(
        source="/c",
        failure_category="transient",
        failure_reason="r",
        error_message="e",
        error_type="E",
        source_hash=None,
        source_size_bytes=None,
        source_mtime_ns=None,
    )
    row = prop_index.get_indexing_failure("/c")
    assert row is not None
    parsed = datetime.fromisoformat(row.last_failed_at)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    assert datetime.now(UTC) - parsed < timedelta(minutes=1)
