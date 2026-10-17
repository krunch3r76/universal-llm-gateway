"""Guards for exhausted extraction-queue rows: backoff retry or dead-letter.

A MapPartialFailureError that is not prompt-too-large must stay claimable after
the fast budget (5) once its backoff elapses, and must stop at the retry
ceiling. Prompt-too-large and any other error at the fast budget must show up
as queue_state dead_letter and must not be claimed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from services.rag.admin_routes import register_admin_routes
from services.rag.extraction import worker_loop
from services.rag.extraction.worker_loop import run_extraction_worker
from services.rag.property_index import PropertyIndex
from services.rag.property_index.queue_policy import QUEUE_RETRY_CEILING

_RETRY_CEILING = 8
_MAP_TIMEOUT = (
    "Extraction pipeline failed: MapPartialFailureError — "
    "TIMEOUT after 900.0s (request pending)"
)
_MAP_OVERSIZE = (
    "Extraction pipeline failed: MapPartialFailureError — "
    "FAILED - Prompt too large for model"
)


async def _property_index(tmp_path: Path) -> PropertyIndex:
    idx = PropertyIndex(db_path=tmp_path / "rag_metadata.db")
    await idx.start()
    return idx


async def _seed(
    idx: PropertyIndex,
    source: str,
    *,
    attempts: int,
    error: str,
    age_minutes: int,
) -> None:
    await idx.enqueue_extraction(source)
    conn = idx._ensure_conn()
    conn.execute(
        "UPDATE extraction_queue SET"
        " attempts = ?,"
        " last_attempt_at = datetime('now', ?),"
        " last_failure_at = datetime('now', ?),"
        " last_error = ?,"
        " last_error_type = 'RuntimeError',"
        " last_failure_category = 'iteration_timeout'"
        " WHERE source = ?",
        (
            attempts,
            f"-{age_minutes + 1} minutes",
            f"-{age_minutes} minutes",
            error,
            source,
        ),
    )
    conn.commit()


@pytest.mark.asyncio
async def test_exhausted_map_timeout_retries_after_backoff(tmp_path: Path) -> None:
    """Attempts=5 MapPartial timeout waits out 15 minutes, then is claimed.

    Two minutes is past the fast-budget minute and must not be enough. The
    ceiling (attempts=8) is not claimed even after the longest backoff.
    """
    idx = await _property_index(tmp_path)
    source = "/data/seals/cursor-timeout.messages.json"
    try:
        await _seed(idx, source, attempts=5, error=_MAP_TIMEOUT, age_minutes=2)
        too_soon = await idx.dequeue_extraction(limit=1, max_attempts=_RETRY_CEILING)
        assert too_soon == []

        conn = idx._ensure_conn()
        conn.execute(
            "UPDATE extraction_queue SET"
            " last_attempt_at = datetime('now', '-17 minutes'),"
            " last_failure_at = datetime('now', '-16 minutes')"
            " WHERE source = ?",
            (source,),
        )
        conn.commit()
        claimed = await idx.dequeue_extraction(limit=1, max_attempts=_RETRY_CEILING)
        assert [row.source for row in claimed] == [source]

        conn.execute(
            "UPDATE extraction_queue SET"
            " attempts = 8,"
            " last_attempt_at = datetime('now', '-241 minutes'),"
            " last_failure_at = datetime('now', '-240 minutes')"
            " WHERE source = ?",
            (source,),
        )
        conn.commit()
        capped = await idx.dequeue_extraction(limit=1, max_attempts=_RETRY_CEILING)
        assert capped == []
        listed = idx.list_extraction_queue_rows(limit=None)
        assert listed[0].state == "dead_letter"
    finally:
        await idx.stop()


@pytest.mark.asyncio
async def test_exhausted_prompt_too_large_is_listed_dead_letter(
    tmp_path: Path,
) -> None:
    """Prompt-too-large at attempts=5 is listed dead_letter and is not claimed."""
    idx = await _property_index(tmp_path)
    source = "/data/seals/cursor-oversize.messages.json"
    try:
        await _seed(idx, source, attempts=5, error=_MAP_OVERSIZE, age_minutes=240)
        claimed = await idx.dequeue_extraction(limit=1, max_attempts=_RETRY_CEILING)
        assert claimed == []
        listed = idx.list_extraction_queue_rows(limit=None)
        assert len(listed) == 1
        assert listed[0].source == source
        assert listed[0].state == "dead_letter"
        assert listed[0].attempts == 5
        breakdown = idx.get_extraction_queue_breakdown()
        assert breakdown.exhausted == 1
    finally:
        await idx.stop()


@pytest.mark.asyncio
async def test_worker_dequeues_at_retry_ceiling() -> None:
    """The worker claims with the retry ceiling, so attempts 5-7 are eligible."""
    property_index = AsyncMock()
    property_index.dequeue_extraction = AsyncMock(return_value=[])
    shutdown = asyncio.Event()

    async def _sleep(*_args: object, **_kwargs: object) -> None:
        shutdown.set()

    config = MagicMock()
    config.knowledge_extraction = MagicMock(pipeline="rag-extract-knowledge")
    with (
        patch.object(worker_loop, "wait_until_extraction_ready", AsyncMock()),
        patch.object(worker_loop, "configure_timeouts"),
        patch.object(
            worker_loop, "probe_stargate_models", AsyncMock(return_value=True)
        ),
        patch.object(worker_loop, "_sleep_or_shutdown", AsyncMock(side_effect=_sleep)),
    ):
        await run_extraction_worker(
            config=config,
            collection_fn=lambda: MagicMock(),
            property_index=property_index,
            event_bus=None,
            shutdown_event=shutdown,
        )
    assert (
        property_index.dequeue_extraction.await_args.kwargs["max_attempts"]
        == QUEUE_RETRY_CEILING
    )


@pytest.mark.asyncio
async def test_redrive_route_arms_timeout_and_lists_oversize(
    tmp_path: Path,
) -> None:
    """POST /extraction/queue/redrive dry_run counts; a write arms one retry."""
    idx = await _property_index(tmp_path)
    timeout_source = "/data/seals/cursor-timeout.messages.json"
    oversize_source = "/data/seals/cursor-oversize.messages.json"
    try:
        await _seed(idx, timeout_source, attempts=5, error=_MAP_TIMEOUT, age_minutes=2)
        await _seed(
            idx, oversize_source, attempts=5, error=_MAP_OVERSIZE, age_minutes=2
        )
        app = FastAPI()
        app.include_router(
            register_admin_routes(
                index_file_fn=lambda *_a, **_k: None,
                get_collection_fn=lambda: None,
                get_watcher_manager_fn=lambda: None,
                get_chroma_fn=lambda: None,
                set_collection_fn=lambda _c: None,
                collection_name="universal_rag",
                get_property_index_fn=lambda: idx,
                get_event_bus_fn=lambda: None,
            )
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            preview = await client.post(
                "/extraction/queue/redrive", json={"dry_run": True}
            )
            assert preview.status_code == 200
            body = preview.json()
            assert body["dry_run"] is True
            assert body["retried"] == [timeout_source]
            assert body["dead_lettered"] == [oversize_source]
            assert body["succeeded"] == []
            assert body["failed_again"] == []

            applied = await client.post(
                "/extraction/queue/redrive", json={"dry_run": False}
            )
            assert applied.status_code == 200
            assert applied.json()["retried"] == [timeout_source]

        claimed = await idx.dequeue_extraction(
            limit=2, max_attempts=QUEUE_RETRY_CEILING
        )
        assert [row.source for row in claimed] == [timeout_source]
        listed = {
            row.source: row.state for row in idx.list_extraction_queue_rows(limit=None)
        }
        assert listed[oversize_source] == "dead_letter"
    finally:
        await idx.stop()
