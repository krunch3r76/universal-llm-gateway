"""Concurrency contracts for event-store analytical reads."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from event_store.store import EventStore


class _Cursor:
    def fetchmany(self, limit: int) -> list[dict[str, Any]]:
        return [{"value": 1}]


class _Reader:
    def execute(self, sql: str, params: tuple[Any, ...]) -> _Cursor:
        if sql == "slow":
            time.sleep(0.2)
        return _Cursor()


@pytest.mark.asyncio
async def test_slow_query_does_not_block_second_read() -> None:
    """A slow reader runs off-loop so a second query can complete promptly."""
    store = EventStore(":memory:")
    await store.open()
    store._reader_connection = lambda: _Reader()  # type: ignore[method-assign]

    started = time.perf_counter()
    slow, fast = await asyncio.gather(
        store.query("slow"),
        store.query("fast"),
    )
    elapsed = time.perf_counter() - started

    assert slow == [{"value": 1}]
    assert fast == [{"value": 1}]
    assert elapsed < 0.35
    await store.close()
