"""Tests for extraction queue operator reset (Slice A)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from services.rag.admin_routes import register_admin_routes
from services.rag.property_index import PropertyIndex


class _FakeCollection:
    def count(self) -> int:
        return 0


async def _property_index(tmp_path: Path) -> PropertyIndex:
    idx = PropertyIndex(db_path=tmp_path / "rag_metadata.db")
    await idx.start()
    return idx


async def _seed_exhausted(idx: PropertyIndex, source: str, *, queued_at: str) -> None:
    await idx.enqueue_extraction(source)

    def _mark_exhausted() -> None:
        conn = idx._ensure_conn()
        conn.execute(
            "UPDATE extraction_queue SET"
            " attempts = 5,"
            " last_attempt_at = datetime('now'),"
            " last_failure_at = datetime('now'),"
            " active_execution_id = 'exec-stale',"
            " last_error = 'permanent failure',"
            " queued_at = ?"
            " WHERE source = ?",
            (queued_at, source),
        )
        conn.commit()

    idx.run_sqlite(_mark_exhausted)


class _RecordingEventBus:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish_nowait(self, event: object) -> None:
        self.events.append(event)


def _reset_app(
    *,
    property_index: PropertyIndex | None,
    event_bus: _RecordingEventBus | None = None,
) -> FastAPI:
    app = FastAPI()
    router = register_admin_routes(
        index_file_fn=lambda *_args, **_kwargs: None,
        get_collection_fn=lambda: _FakeCollection(),
        get_watcher_manager_fn=lambda: None,
        get_chroma_fn=lambda: None,
        set_collection_fn=lambda _collection: None,
        collection_name="universal_rag",
        get_property_index_fn=lambda: property_index,
        get_event_bus_fn=lambda: event_bus,
    )
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_reset_exhausted_row_is_dequeue_eligible(tmp_path: Path) -> None:
    """Exhausted row becomes ready and is returned by dequeue_extraction."""
    idx = await _property_index(tmp_path)
    source = "/data/seal-a.messages.json"
    try:
        await _seed_exhausted(idx, source, queued_at="2026-10-01 00:00:00")
        rows, reset_count = await idx.reset_extraction_queue_sources([source])
        assert reset_count == 1
        assert rows[0].outcome == "reset"
        assert rows[0].attempts == 0

        listed = idx.list_extraction_queue_rows(max_attempts=5)
        assert listed[0].state == "ready"
        claims = await idx.dequeue_extraction(limit=1, max_attempts=5)
        assert len(claims) == 1
        assert claims[0].source == source
    finally:
        await idx.stop()


@pytest.mark.asyncio
async def test_reset_leaves_unlisted_source_unchanged(tmp_path: Path) -> None:
    """Only named sources are reset; siblings keep attempts and diagnostics."""
    idx = await _property_index(tmp_path)
    listed_source = "/data/listed.messages.json"
    other_source = "/data/other.messages.json"
    try:
        await _seed_exhausted(idx, listed_source, queued_at="2026-10-01 00:00:00")
        await _seed_exhausted(idx, other_source, queued_at="2026-10-02 00:00:00")
        before = idx.list_extraction_queue_rows(max_attempts=5, limit=None)
        other_before = next(r for r in before if r.source == other_source)

        await idx.reset_extraction_queue_sources([listed_source])

        after = idx.list_extraction_queue_rows(max_attempts=5, limit=None)
        other_after = next(r for r in after if r.source == other_source)
        assert other_after.attempts == 5
        assert other_after.last_error == other_before.last_error
        assert other_after.queued_at == other_before.queued_at
    finally:
        await idx.stop()


@pytest.mark.asyncio
async def test_reset_second_call_is_noop(tmp_path: Path) -> None:
    """Second reset is idempotent and emits only one bus event."""
    idx = await _property_index(tmp_path)
    source = "/data/idempotent.messages.json"
    bus = _RecordingEventBus()
    app = _reset_app(property_index=idx, event_bus=bus)
    try:
        await _seed_exhausted(idx, source, queued_at="2026-10-01 00:00:00")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post(
                "/extraction/queue/reset", json={"sources": [source]}
            )
            assert first.status_code == 200
            assert first.json()["reset_count"] == 1

            second = await client.post(
                "/extraction/queue/reset", json={"sources": [source]}
            )
            assert second.status_code == 200
            body = second.json()
            assert body["reset_count"] == 0
            assert body["results"][0]["outcome"] == "not_exhausted"
            assert body["results"][0]["attempts"] == 0

        row = idx.list_extraction_queue_rows(max_attempts=5)[0]
        assert row.attempts == 0
        assert len(bus.events) == 1
    finally:
        await idx.stop()
