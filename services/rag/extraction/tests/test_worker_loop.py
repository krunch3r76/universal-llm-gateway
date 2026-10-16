"""Unit tests for extraction worker loop failure classification and cancel-before-submit."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.rag.extraction import chroma_source, worker_loop
from services.rag.extraction.worker_loop import (
    _MAX_QUEUE_ATTEMPTS,
    is_map_iteration_timeout_error,
    is_map_terminal_pipeline_error,
    run_extraction_worker,
)
from services.rag.knowledge_extractor import CancelExtractionOutcome
from services.rag.property_index import PropertyIndex
from services.rag.property_index._spec import ExtractionQueueClaim

_MAP_TIMEOUT_ERR = RuntimeError(
    "Extraction pipeline failed: MapPartialFailureError — "
    "TIMEOUT after 900.0s (request pending)"
)
_MAP_FAILED_ERR = RuntimeError(
    "Extraction pipeline failed: MapPartialFailureError — FAILED - worker died"
)


def test_is_map_iteration_timeout_error_positive() -> None:
    assert is_map_iteration_timeout_error(_MAP_TIMEOUT_ERR)


def test_is_map_iteration_timeout_error_other_runtime() -> None:
    assert not is_map_iteration_timeout_error(RuntimeError("other failure"))


def test_is_map_iteration_timeout_error_non_runtime() -> None:
    assert not is_map_iteration_timeout_error(ValueError("MapPartialFailureError"))


def test_is_map_terminal_pipeline_error_failed_iteration() -> None:
    assert is_map_terminal_pipeline_error(_MAP_FAILED_ERR)
    assert not is_map_iteration_timeout_error(_MAP_FAILED_ERR)


async def _property_index(tmp_path: Path) -> PropertyIndex:
    idx = PropertyIndex(db_path=tmp_path / "rag_metadata.db")
    await idx.start()
    return idx


@pytest.mark.asyncio
async def test_map_timeout_failure_increments_attempt_until_exhausted(
    tmp_path: Path,
) -> None:
    """Iteration-timeout failures burn attempts; dequeue stops at max_attempts (_MAX_QUEUE_ATTEMPTS)."""
    idx = await _property_index(tmp_path)
    try:
        await idx.enqueue_extraction("/cursor-fc9.messages.json")
        for _ in range(_MAX_QUEUE_ATTEMPTS):
            claims = await idx.dequeue_extraction(
                limit=1, max_attempts=_MAX_QUEUE_ATTEMPTS
            )
            assert len(claims) == 1
            await idx.fail_extraction(
                claims[0].source,
                increment_attempt=True,
                failure_category="iteration_timeout",
                error=str(_MAP_TIMEOUT_ERR),
                error_type="RuntimeError",
            )
            conn = idx._ensure_conn()
            conn.execute(
                "UPDATE extraction_queue SET"
                " last_attempt_at = datetime('now', '-3 minutes'),"
                " last_failure_at = datetime('now', '-2 minutes')"
                " WHERE source = ?",
                (claims[0].source,),
            )
            conn.commit()

        exhausted = await idx.dequeue_extraction(
            limit=1, max_attempts=_MAX_QUEUE_ATTEMPTS
        )
        assert exhausted == []
        rows = idx.list_extraction_queue_rows(max_attempts=_MAX_QUEUE_ATTEMPTS)
        assert len(rows) == 1
        assert rows[0].state == "exhausted"
        assert rows[0].attempts == _MAX_QUEUE_ATTEMPTS
    finally:
        await idx.stop()


@pytest.mark.asyncio
async def test_worker_loop_map_timeout_records_increment_attempt_true() -> None:
    """Worker loop classifies map stall RuntimeError with increment_attempt=True."""
    claim = ExtractionQueueClaim(
        source="/s.json",
        attempts=1,
        queued_at="2026-01-01",
        claimed_at="2026-01-02",
        active_execution_id="ecf7f2a0-8866-462a-95ab-630d32cc83a9",
    )
    property_index = AsyncMock()
    property_index.dequeue_extraction = AsyncMock(
        side_effect=[[claim], []]
    )
    property_index.fail_extraction = AsyncMock()
    shutdown = asyncio.Event()

    config = MagicMock()
    ke = MagicMock(pipeline="rag-extract-knowledge")
    config.knowledge_extraction = ke

    async def _raise_timeout(*_a: object, **_k: object) -> None:
        shutdown.set()
        raise _MAP_TIMEOUT_ERR

    with (
        patch.object(worker_loop, "wait_until_extraction_ready", AsyncMock()),
        patch.object(worker_loop, "configure_timeouts"),
        patch.object(worker_loop, "extract_source", AsyncMock(side_effect=_raise_timeout)),
        patch.object(worker_loop, "_sleep_or_shutdown", AsyncMock()),
    ):
        await run_extraction_worker(
            config=config,
            collection_fn=lambda: MagicMock(),
            property_index=property_index,
            event_bus=None,
            shutdown_event=shutdown,
        )

    property_index.fail_extraction.assert_awaited()
    _args, kwargs = property_index.fail_extraction.await_args
    assert kwargs["increment_attempt"] is True
    assert kwargs["failure_category"] == "iteration_timeout"


@pytest.mark.asyncio
async def test_worker_loop_httpx_timeout_still_capacity_class() -> None:
    """Real capacity signals keep increment_attempt=False."""
    claim = ExtractionQueueClaim(
        source="/s.json",
        attempts=0,
        queued_at="t",
        claimed_at="t",
    )
    property_index = AsyncMock()
    property_index.dequeue_extraction = AsyncMock(side_effect=[[claim], []])
    property_index.fail_extraction = AsyncMock()
    shutdown = asyncio.Event()

    config = MagicMock()
    ke = MagicMock(pipeline="rag-extract-knowledge")
    config.knowledge_extraction = ke

    async def _raise_httpx_timeout(*_a: object, **_k: object) -> None:
        shutdown.set()
        raise httpx.TimeoutException("read timed out")

    with (
        patch.object(worker_loop, "wait_until_extraction_ready", AsyncMock()),
        patch.object(worker_loop, "configure_timeouts"),
        patch.object(
            worker_loop, "extract_source", AsyncMock(side_effect=_raise_httpx_timeout)
        ),
        patch.object(worker_loop, "_sleep_or_shutdown", AsyncMock()),
    ):
        await run_extraction_worker(
            config=config,
            collection_fn=lambda: MagicMock(),
            property_index=property_index,
            event_bus=None,
            shutdown_event=shutdown,
        )

    _args, kwargs = property_index.fail_extraction.await_args
    assert kwargs["increment_attempt"] is False
    assert kwargs["failure_category"] == "capacity"


@pytest.mark.asyncio
async def test_reclaim_cancel_retains_pointer_when_stargate_down(
    tmp_path: Path,
) -> None:
    """Unknown cancel outcome leaves active_execution_id for startup recovery."""
    idx = await _property_index(tmp_path)
    try:
        await idx.enqueue_extraction("/s.json")
        await idx.record_extraction_execution_id("/s.json", "stale-exec-id")
        with patch.object(
            chroma_source,
            "cancel_extraction_execution",
            AsyncMock(return_value=CancelExtractionOutcome.UNKNOWN),
        ):
            await chroma_source.reclaim_cancel_stored_execution(
                source="/s.json",
                prior_execution_id="stale-exec-id",
                property_index=idx,
            )
        conn = idx._ensure_conn()
        row = conn.execute(
            "SELECT active_execution_id FROM extraction_queue WHERE source = ?",
            ("/s.json",),
        ).fetchone()
        assert row[0] == "stale-exec-id"
    finally:
        await idx.stop()


@pytest.mark.asyncio
async def test_reclaim_cancel_clears_pointer_on_404(tmp_path: Path) -> None:
    idx = await _property_index(tmp_path)
    try:
        await idx.enqueue_extraction("/s.json")
        await idx.record_extraction_execution_id("/s.json", "gone-exec-id")
        with patch.object(
            chroma_source,
            "cancel_extraction_execution",
            AsyncMock(return_value=CancelExtractionOutcome.ALREADY_GONE),
        ):
            await chroma_source.reclaim_cancel_stored_execution(
                source="/s.json",
                prior_execution_id="gone-exec-id",
                property_index=idx,
            )
        conn = idx._ensure_conn()
        row = conn.execute(
            "SELECT active_execution_id FROM extraction_queue WHERE source = ?",
            ("/s.json",),
        ).fetchone()
        assert row[0] is None
    finally:
        await idx.stop()


@pytest.mark.asyncio
async def test_sequential_reclaim_cancel_is_idempotent(tmp_path: Path) -> None:
    """Two reclaim cancels on the same source both attempt cancel; pointer clears when confirmed."""
    idx = await _property_index(tmp_path)
    try:
        cancel_mock = AsyncMock(
            side_effect=[
                CancelExtractionOutcome.CANCELLED,
                CancelExtractionOutcome.ALREADY_GONE,
            ]
        )
        await idx.enqueue_extraction("/s.json")
        await idx.record_extraction_execution_id("/s.json", "exec-a")
        with patch.object(
            chroma_source, "cancel_extraction_execution", cancel_mock
        ):
            await chroma_source.reclaim_cancel_stored_execution(
                source="/s.json",
                prior_execution_id="exec-a",
                property_index=idx,
            )
            await idx.record_extraction_execution_id("/s.json", "exec-b")
            await chroma_source.reclaim_cancel_stored_execution(
                source="/s.json",
                prior_execution_id="exec-b",
                property_index=idx,
            )
        assert cancel_mock.await_count == 2
    finally:
        await idx.stop()


@pytest.mark.asyncio
async def test_worker_reclaim_cancels_before_extract_source(
    tmp_path: Path,
) -> None:
    """Claim lifecycle cancel runs before extract_source (including early-complete paths)."""
    claim = ExtractionQueueClaim(
        source="/s.json",
        attempts=1,
        queued_at="t",
        claimed_at="t",
        active_execution_id="failed-mid-job",
    )
    property_index = AsyncMock()
    property_index.dequeue_extraction = AsyncMock(side_effect=[[claim], []])
    shutdown = asyncio.Event()

    async def _complete(_source: str) -> None:
        shutdown.set()

    property_index.complete_extraction = AsyncMock(side_effect=_complete)
    config = MagicMock()
    ke = MagicMock(pipeline="rag-extract-knowledge")
    config.knowledge_extraction = ke

    reclaim_mock = AsyncMock()
    extract_mock = AsyncMock(return_value=(True, True, "", "", ""))

    with (
        patch.object(worker_loop, "wait_until_extraction_ready", AsyncMock()),
        patch.object(worker_loop, "configure_timeouts"),
        patch.object(worker_loop, "reclaim_cancel_stored_execution", reclaim_mock),
        patch.object(worker_loop, "extract_source", extract_mock),
        patch.object(worker_loop, "_sleep_or_shutdown", AsyncMock()),
    ):
        await run_extraction_worker(
            config=config,
            collection_fn=lambda: MagicMock(),
            property_index=property_index,
            event_bus=None,
            shutdown_event=shutdown,
        )

    reclaim_mock.assert_awaited_once_with(
        source="/s.json",
        prior_execution_id="failed-mid-job",
        property_index=property_index,
    )
    extract_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_loop_map_failed_still_increments_attempt() -> None:
    claim = ExtractionQueueClaim(
        source="/s.json",
        attempts=1,
        queued_at="t",
        claimed_at="t",
    )
    property_index = AsyncMock()
    property_index.dequeue_extraction = AsyncMock(side_effect=[[claim], []])
    property_index.fail_extraction = AsyncMock()
    shutdown = asyncio.Event()

    config = MagicMock()
    ke = MagicMock(pipeline="rag-extract-knowledge")
    config.knowledge_extraction = ke

    async def _raise_failed(*_a: object, **_k: object) -> None:
        shutdown.set()
        raise _MAP_FAILED_ERR

    with (
        patch.object(worker_loop, "wait_until_extraction_ready", AsyncMock()),
        patch.object(worker_loop, "configure_timeouts"),
        patch.object(worker_loop, "reclaim_cancel_stored_execution", AsyncMock()),
        patch.object(worker_loop, "extract_source", AsyncMock(side_effect=_raise_failed)),
        patch.object(worker_loop, "_sleep_or_shutdown", AsyncMock()),
    ):
        await run_extraction_worker(
            config=config,
            collection_fn=lambda: MagicMock(),
            property_index=property_index,
            event_bus=None,
            shutdown_event=shutdown,
        )

    _args, kwargs = property_index.fail_extraction.await_args
    assert kwargs["increment_attempt"] is True
    assert kwargs["failure_category"] == "iteration_timeout"


@pytest.mark.asyncio
async def test_dequeue_returns_active_execution_id_for_claim(tmp_path: Path) -> None:
    """Claims surface stored execution ids for cancel-before-submit on reclaim."""
    idx = await _property_index(tmp_path)
    try:
        await idx.enqueue_extraction("/s.json")
        conn = idx._ensure_conn()
        conn.execute(
            "UPDATE extraction_queue SET active_execution_id = ? WHERE source = ?",
            ("stored-id", "/s.json"),
        )
        conn.commit()
        claims = await idx.dequeue_extraction(limit=1, max_attempts=5)
        assert claims[0].active_execution_id == "stored-id"
    finally:
        await idx.stop()
