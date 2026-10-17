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

    def _mark() -> None:
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

    idx.run_sqlite(_mark)


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

        def _age_for_claim() -> None:
            conn = idx._ensure_conn()
            conn.execute(
                "UPDATE extraction_queue SET"
                " last_attempt_at = datetime('now', '-17 minutes'),"
                " last_failure_at = datetime('now', '-16 minutes')"
                " WHERE source = ?",
                (source,),
            )
            conn.commit()

        idx.run_sqlite(_age_for_claim)
        claimed = await idx.dequeue_extraction(limit=1, max_attempts=_RETRY_CEILING)
        assert [row.source for row in claimed] == [source]

        def _bump_to_ceiling() -> None:
            conn = idx._ensure_conn()
            conn.execute(
                "UPDATE extraction_queue SET"
                " attempts = 8,"
                " last_attempt_at = datetime('now', '-241 minutes'),"
                " last_failure_at = datetime('now', '-240 minutes')"
                " WHERE source = ?",
                (source,),
            )
            conn.commit()

        idx.run_sqlite(_bump_to_ceiling)
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
async def test_capacity_below_ceiling_stays_claimable_after_redrive(
    tmp_path: Path,
) -> None:
    """Capacity at attempts 5 and 7 stays claimable; attempts 8 is dead_letter.

    A redrive must not stamp category=capacity as dead_letter below the ceiling.
    After the write, those rows are still claimed once their backoff has elapsed.
    """
    idx = await _property_index(tmp_path)
    sources = {
        5: "/data/cap-5.json",
        7: "/data/cap-7.json",
        8: "/data/cap-8.json",
    }
    try:
        for attempts, source in sources.items():
            await _seed(
                idx, source, attempts=attempts, error="HTTP 503", age_minutes=241
            )

            def _capacity() -> None:
                conn = idx._ensure_conn()
                conn.execute(
                    "UPDATE extraction_queue SET last_failure_category = 'capacity'"
                    " WHERE source = ?",
                    (source,),
                )
                conn.commit()

            idx.run_sqlite(_capacity)
        outcome = await idx.redrive_exhausted_once(dry_run=False)
        assert sources[5] not in outcome.dead_lettered
        assert sources[7] not in outcome.dead_lettered
        assert sources[8] in outcome.dead_lettered
        listed = {row.source: row for row in idx.list_extraction_queue_rows(limit=None)}
        assert listed[sources[5]].last_failure_category == "capacity"
        assert listed[sources[7]].last_failure_category == "capacity"
        assert listed[sources[8]].state == "dead_letter"
        claims = await idx.dequeue_extraction(limit=3, max_attempts=QUEUE_RETRY_CEILING)
        claimed = {row.source for row in claims}
        assert sources[5] in claimed
        assert sources[7] in claimed
        assert sources[8] not in claimed
    finally:
        await idx.stop()


@pytest.mark.asyncio
async def test_redrive_keeps_execution_id_and_skips_in_flight(
    tmp_path: Path,
) -> None:
    """Redrive does not clear active_execution_id and does not arm an in-flight row.

    A claim that lands between the list snapshot and the UPDATE must keep its
    execution id. The write's WHERE excludes that in-flight shape.
    """
    idx = await _property_index(tmp_path)
    armed = "/data/armed.json"
    inflight = "/data/inflight.json"
    try:
        await _seed(idx, armed, attempts=5, error=_MAP_TIMEOUT, age_minutes=16)
        await _seed(idx, inflight, attempts=5, error=_MAP_TIMEOUT, age_minutes=16)

        def _mark_inflight() -> None:
            conn = idx._ensure_conn()
            conn.execute(
                "UPDATE extraction_queue SET active_execution_id = 'exec-keep'"
                " WHERE source = ?",
                (armed,),
            )
            conn.execute(
                "UPDATE extraction_queue SET"
                " active_execution_id = 'exec-live',"
                " last_attempt_at = datetime('now'),"
                " last_failure_at = datetime('now', '-5 minutes')"
                " WHERE source = ?",
                (inflight,),
            )
            conn.commit()

        idx.run_sqlite(_mark_inflight)
        outcome = await idx.redrive_exhausted_once(dry_run=False)
        assert armed in outcome.retried
        assert inflight not in outcome.retried

        def _read_exec_ids() -> dict[str, object]:
            conn = idx._ensure_conn()
            return {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT source, active_execution_id FROM extraction_queue"
                ).fetchall()
            }

        ids = idx.run_sqlite(_read_exec_ids)
        assert ids[armed] == "exec-keep"
        assert ids[inflight] == "exec-live"
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
