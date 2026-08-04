"""Watcher initial-reindex transient retry behavior (rag-initial-reindex-504-retry)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.rag.config import WatchDirectory
from services.rag.models import IndexResult
from services.rag.property_index import IndexingFailure
from services.rag.watcher_manager import WatcherManager


def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "http://test")
    resp = httpx.Response(status_code=status, request=req)
    return httpx.HTTPStatusError("upstream failure", request=req, response=resp)


async def _run_initial_reindex(
    tmp_path: Path,
    *,
    index_fn: AsyncMock,
    property_index: object | None = None,
) -> tuple[list[object], object | None]:
    watch_dir = tmp_path / "docs"
    watch_dir.mkdir()
    (watch_dir / "note.md").write_text("hello", encoding="utf-8")

    emitted: list[object] = []

    async def capture_emit(event: object) -> None:
        emitted.append(event)

    wm = WatcherManager(
        index_fn=index_fn,
        event_bus=MagicMock(),
        property_index=property_index,
        index_workers=1,
    )
    wm._emit = capture_emit  # type: ignore[method-assign]

    watch_directory = WatchDirectory(path=str(watch_dir), recursive=False)
    with patch(
        "services.rag.watcher_manager.initial_reindex.asyncio.sleep",
        new=AsyncMock(),
    ):
        await wm._initial_reindex(
            watch_dir,
            watch_directory,
            (".md",),
        )

    complete = next(
        (e for e in emitted if e.signal == "rag.watch.initial.complete"),
        None,
    )
    return emitted, complete


@pytest.mark.asyncio
async def test_transient_retry_succeeds_without_terminal_failure(
    tmp_path: Path,
) -> None:
    index_fn = AsyncMock(
        side_effect=[
            _http_error(504),
            _http_error(504),
            IndexResult(deleted=0, indexed=1, unchanged=False, file="note.md"),
        ]
    )
    emitted, complete = await _run_initial_reindex(tmp_path, index_fn=index_fn)

    assert index_fn.await_count == 3
    failed_events = [e for e in emitted if e.signal == "rag.file.indexing.failed"]
    assert failed_events == []
    assert complete is not None
    assert complete.payload["errors"] == 0
    assert complete.payload["reindexed"] == 1


@pytest.mark.asyncio
async def test_transient_budget_exhaustion_emits_one_terminal_failure(
    tmp_path: Path,
) -> None:
    index_fn = AsyncMock(side_effect=[_http_error(504)] * 3)
    emitted, complete = await _run_initial_reindex(tmp_path, index_fn=index_fn)

    assert index_fn.await_count == 3
    failed_events = [e for e in emitted if e.signal == "rag.file.indexing.failed"]
    assert len(failed_events) == 1
    assert complete is not None
    assert complete.payload["errors"] == 1


@pytest.mark.asyncio
async def test_permanent_failure_not_retried(tmp_path: Path) -> None:
    index_fn = AsyncMock(side_effect=PermissionError("denied"))
    emitted, complete = await _run_initial_reindex(tmp_path, index_fn=index_fn)

    assert index_fn.await_count == 1
    failed_events = [e for e in emitted if e.signal == "rag.file.indexing.failed"]
    assert len(failed_events) == 1
    assert complete is not None
    assert complete.payload["errors"] == 1


@pytest.mark.asyncio
async def test_property_index_none_still_emits_terminal_failure(
    tmp_path: Path,
) -> None:
    index_fn = AsyncMock(side_effect=[_http_error(504)] * 3)
    emitted, complete = await _run_initial_reindex(
        tmp_path,
        index_fn=index_fn,
        property_index=None,
    )

    failed_events = [e for e in emitted if e.signal == "rag.file.indexing.failed"]
    assert len(failed_events) == 1
    assert complete is not None
    assert complete.payload["errors"] == 1


@pytest.mark.asyncio
async def test_watcher_retry_does_not_call_wait_for_model_ready(
    tmp_path: Path,
) -> None:
    index_fn = AsyncMock(side_effect=[_http_error(504)] * 3)
    with patch(
        "services.rag.embeddings.batch_post._wait_for_model_ready",
        new=AsyncMock(),
    ) as wait_ready:
        await _run_initial_reindex(tmp_path, index_fn=index_fn)
        wait_ready.assert_not_called()


@pytest.mark.asyncio
async def test_permanent_failure_skipped_on_reconcile_ac_b4_t5(tmp_path: Path) -> None:
    source_path = tmp_path / "doc.md"
    source_path.write_text("body", encoding="utf-8")
    stat = source_path.stat()
    prop_index = MagicMock()
    prop_index.get_indexing_failure.return_value = IndexingFailure(
        source=str(source_path),
        failure_category="permanent",
        failure_reason="http_client_error",
        error_message="HTTPStatusError: 404",
        error_type="HTTPStatusError",
        first_failed_at="2026-01-01T00:00:00",
        last_failed_at="2026-01-01T00:00:00",
        attempt_count=1,
        source_hash="abc",
        source_size_bytes=stat.st_size,
        source_mtime_ns=stat.st_mtime_ns,
    )
    emitted: list[object] = []

    async def capture_emit(event: object) -> None:
        emitted.append(event)

    wm = WatcherManager(index_fn=AsyncMock(), property_index=prop_index)
    wm._emit = capture_emit  # type: ignore[method-assign]

    allowed = await wm._should_attempt(source_path)
    assert allowed is False
    skipped = [e for e in emitted if e.signal == "rag.file.indexing.failure.skipped"]
    assert len(skipped) == 1
