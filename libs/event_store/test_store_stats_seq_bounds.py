"""Seq bounds use split subqueries (not MAX(seq)-MIN(seq) in one expression)."""

from __future__ import annotations

import asyncio

import pytest

from event_store.store import EventStore
from event_store.store_stats import query_seq_bounds


@pytest.mark.offline
def test_query_seq_bounds_split_subqueries() -> None:
    async def _run() -> dict[str, int | None]:
        store = EventStore(":memory:")
        await store.open()
        try:
            await store.insert_events(
                [
                    {
                        "signal": "a",
                        "role": "observation",
                        "scope": "global",
                        "ts_unix_ms": 1,
                        "timestamp": "t",
                        "source": "s",
                        "payload": {},
                    },
                    {
                        "signal": "b",
                        "role": "observation",
                        "scope": "global",
                        "ts_unix_ms": 2,
                        "timestamp": "t",
                        "source": "s",
                        "payload": {},
                    },
                ]
            )
            return await query_seq_bounds(store)
        finally:
            await store.close()

    bounds = asyncio.run(_run())
    assert bounds["min_seq"] == 1
    assert bounds["max_seq"] == 2
