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
    run_extraction_worker,
)
from services.rag.property_index import PropertyIndex
from services.rag.property_index._spec import ExtractionQueueClaim

_MAP_TIMEOUT_ERR = RuntimeError(
    "Extraction pipeline failed: MapPartialFailureError — "
    "TIMEOUT after 900.0s (request pending)"
)


def test_is_map_iteration_timeout_error_positive() -> None:
    assert is_map_iteration_timeout_error(_MAP_TIMEOUT_ERR)


def test_is_map_iteration_timeout_error_other_runtime() -> None:
    assert not is_map_iteration_timeout_error(RuntimeError("other failure"))


def test_is_map_iteration_timeout_error_non_runtime() -> None:
    assert not is_map_iteration_timeout_error(ValueError("MapPartialFailureError"))


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
async def test_cancel_stale_execution_swallows_404_still_submits(
    tmp_path: Path,
) -> None:
    """Stargate down / unknown id on cancel must not block submit; pointer cleared."""
    idx = await _property_index(tmp_path)
    try:
        await idx.enqueue_extraction("/s.json")
        await idx.record_extraction_execution_id("/s.json", "stale-exec-id")

        collection = MagicMock()
        collection.get.return_value = {
            "ids": ["c1"],
            "documents": ["doc"],
            "metadatas": [{"source": "/s.json"}],
        }
        config = MagicMock(schema_version=1, extraction_model="m", pipeline="p")

        mock_client = MagicMock()
        mock_client.delete = AsyncMock(side_effect=ConnectionError("stargate down"))

        with (
            patch(
                "services.rag.knowledge_extractor._client",
                mock_client,
            ),
            patch.object(
                chroma_source,
                "submit_extraction_pipeline",
                AsyncMock(return_value="new-exec"),
            ) as submit_mock,
            patch.object(
                chroma_source,
                "poll_extraction_result",
                AsyncMock(return_value=([], {})),
            ),
            patch.object(
                chroma_source,
                "run_chroma",
                AsyncMock(side_effect=lambda fn, *a, **k: fn(*a, **k)),
            ),
        ):
            await chroma_source.extract_source(
                "/s.json",
                collection=collection,
                config=config,
                rag_config=MagicMock(get_scope_for_path=lambda _s: "all"),
                property_index=idx,
                prior_execution_id="stale-exec-id",
            )

        submit_mock.assert_awaited_once()
        conn = idx._ensure_conn()
        row = conn.execute(
            "SELECT active_execution_id FROM extraction_queue WHERE source = ?",
            ("/s.json",),
        ).fetchone()
        assert row[0] is None
    finally:
        await idx.stop()


@pytest.mark.asyncio
async def test_second_claim_cancel_then_submit_is_safe(tmp_path: Path) -> None:
    """Overlapping reclaim paths cancel-then-submit without skipping submit."""
    idx = await _property_index(tmp_path)
    try:
        stale_id = "exec-a"
        collection = MagicMock()
        collection.get.return_value = {
            "ids": ["c1"],
            "documents": ["d"],
            "metadatas": [{}],
        }
        config = MagicMock(schema_version=1, extraction_model="m", pipeline="p")
        cancel_mock = AsyncMock()
        submit_mock = AsyncMock(side_effect=["exec-b", "exec-c"])

        async def _run_extract(prior: str | None) -> None:
            with (
                patch.object(chroma_source, "cancel_extraction_execution", cancel_mock),
                patch.object(
                    chroma_source, "submit_extraction_pipeline", submit_mock
                ),
                patch.object(
                    chroma_source,
                    "poll_extraction_result",
                    AsyncMock(return_value=([], {})),
                ),
                patch.object(
                    chroma_source,
                    "run_chroma",
                    AsyncMock(side_effect=lambda fn, *a, **k: fn(*a, **k)),
                ),
            ):
                await chroma_source.extract_source(
                    "/s.json",
                    collection=collection,
                    config=config,
                    rag_config=MagicMock(get_scope_for_path=lambda _s: "all"),
                    property_index=idx,
                    prior_execution_id=prior,
                )

        await idx.enqueue_extraction("/s.json")
        await _run_extract(stale_id)
        await idx.record_extraction_execution_id("/s.json", "exec-b")
        await _run_extract("exec-b")

        assert cancel_mock.await_count == 2
        assert submit_mock.await_count == 2
    finally:
        await idx.stop()


@pytest.mark.asyncio
async def test_mid_job_poll_failure_then_reclaim_cancels_before_submit(
    tmp_path: Path,
) -> None:
    """After poll failure, the next claim cancels stored execution before a new submit."""
    idx = await _property_index(tmp_path)
    try:
        await idx.enqueue_extraction("/s.json")
        await idx.record_extraction_execution_id("/s.json", "failed-mid-job")

        collection = MagicMock()
        collection.get.return_value = {
            "ids": ["c1"],
            "documents": ["d"],
            "metadatas": [{}],
        }
        config = MagicMock(schema_version=1, extraction_model="m", pipeline="p")
        cancel_mock = AsyncMock()
        submit_mock = AsyncMock(return_value="replacement-exec")

        with (
            patch.object(chroma_source, "cancel_extraction_execution", cancel_mock),
            patch.object(chroma_source, "submit_extraction_pipeline", submit_mock),
            patch.object(
                chroma_source,
                "poll_extraction_result",
                AsyncMock(return_value=([], {})),
            ),
            patch.object(
                chroma_source,
                "run_chroma",
                AsyncMock(side_effect=lambda fn, *a, **k: fn(*a, **k)),
            ),
        ):
            await chroma_source.extract_source(
                "/s.json",
                collection=collection,
                config=config,
                rag_config=MagicMock(get_scope_for_path=lambda _s: "all"),
                property_index=idx,
                prior_execution_id="failed-mid-job",
            )

        cancel_mock.assert_awaited_once_with("failed-mid-job")
        submit_mock.assert_awaited_once()
        conn = idx._ensure_conn()
        cleared = conn.execute(
            "SELECT active_execution_id FROM extraction_queue WHERE source = ?",
            ("/s.json",),
        ).fetchone()[0]
        assert cleared is None
    finally:
        await idx.stop()


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
