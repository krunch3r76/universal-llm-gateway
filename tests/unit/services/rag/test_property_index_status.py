"""Tests for PropertyIndex bounded status snapshot read helpers."""

from __future__ import annotations

import asyncio

from services.rag.property_index import PropertyIndex


def _start_index(tmp_path) -> PropertyIndex:
    db_path = tmp_path / "rag_metadata.db"
    index = PropertyIndex(db_path)
    asyncio.run(index.start())
    return index


def test_pending_snapshot_respects_limit_and_count(tmp_path) -> None:
    """Pending snapshot returns full count with bounded sample rows."""
    index = _start_index(tmp_path)
    try:
        asyncio.run(index.mark_pending("/tmp/a.md"))
        asyncio.run(index.mark_pending("/tmp/b.md"))
        asyncio.run(index.mark_pending("/tmp/c.md"))
        snapshot = index.get_pending_snapshot(sample_limit=2)
        assert snapshot.count == 3
        assert snapshot.sample == ["/tmp/a.md", "/tmp/b.md"]
    finally:
        asyncio.run(index.stop())


def test_pending_snapshot_zero_limit_returns_empty_sample(tmp_path) -> None:
    """sample_limit=0 keeps count but omits pending sample payload values."""
    index = _start_index(tmp_path)
    try:
        asyncio.run(index.mark_pending("/tmp/a.md"))
        snapshot = index.get_pending_snapshot(sample_limit=0)
        assert snapshot.count == 1
        assert snapshot.sample == []
    finally:
        asyncio.run(index.stop())


def test_failure_snapshot_wraps_existing_count_methods(tmp_path) -> None:
    """Failure snapshot mirrors get_failed_count and get_permanent_count output."""
    index = _start_index(tmp_path)
    try:
        asyncio.run(
            index.record_failure("chunk-1", "/tmp/a.md", "transient", permanent=False)
        )
        asyncio.run(
            index.record_failure("chunk-2", "/tmp/b.md", "permanent", permanent=True)
        )
        snapshot = index.get_failure_snapshot()
        assert snapshot.failed_extractions_count == 2
        assert snapshot.failed_extractions_permanent_count == 1
    finally:
        asyncio.run(index.stop())
