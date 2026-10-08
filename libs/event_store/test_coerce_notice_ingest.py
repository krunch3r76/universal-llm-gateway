"""Coercion notices fan out on the ingest path like drop notices."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from event_store.ingest import IngestServer
from event_store.store import EventStore


@pytest.mark.asyncio
@pytest.mark.offline
async def test_one_coerced_batch_emits_one_coerced_ingest(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    await store.open()
    subscriber: asyncio.Queue[dict] = asyncio.Queue()
    server = IngestServer(
        store,
        str(tmp_path / "ingest.sock"),
        {subscriber},
        drop_notice_interval_sec=0.0,
    )
    await server.start()
    try:
        await server._db_queue.put(
            {
                "signal": "demo.null.role",
                "role": None,
                "scope": "global",
                "ts_unix_ms": 1000,
                "timestamp": "2026-01-01T00:00:00Z",
                "source": "producer-a",
                "payload": {},
            }
        )
        seen: list[dict] = []
        deadline = asyncio.get_running_loop().time() + 2
        while asyncio.get_running_loop().time() < deadline:
            try:
                seen.append(await asyncio.wait_for(subscriber.get(), timeout=0.2))
            except TimeoutError:
                if any(ev.get("signal") == "events.coerced.ingest" for ev in seen):
                    break
        notices = [ev for ev in seen if ev.get("signal") == "events.coerced.ingest"]
        assert len(notices) == 1
        assert notices[0]["payload"]["field"] == "role"
        assert notices[0]["payload"]["source"] == "producer-a"
        assert notices[0]["payload"]["count"] == 1
    finally:
        await server.stop()
        await store.close()
