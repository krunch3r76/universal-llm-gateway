"""insert_events coerces explicit null string fields to ingest defaults."""

from __future__ import annotations

import pytest

from event_store.store import EventStore


@pytest.mark.asyncio
async def test_insert_events_null_role_becomes_observation() -> None:
    store = EventStore(":memory:")
    await store.open()
    try:
        accepted = await store.insert_events(
            [
                {
                    "signal": "demo.null.role",
                    "role": None,
                    "scope": "global",
                    "ts_unix_ms": 1000,
                    "timestamp": "2026-01-01T00:00:00Z",
                    "source": "pytest",
                    "payload": {},
                }
            ]
        )
        assert len(accepted) == 1
        rows = await store.query("SELECT role FROM events", ())
        assert len(rows) == 1
        assert rows[0]["role"] == "observation"
    finally:
        await store.close()
