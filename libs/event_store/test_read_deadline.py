"""Server-side read deadline interrupts a slow reader so a fast read can run."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from event_store.errors import EventStoreReadDeadlineError
from event_store.store import EventStore


class _Cursor:
    def fetchmany(self, limit: int) -> list[dict[str, Any]]:
        del limit
        return [{"value": 1}]


class _Conn:
    def __init__(self) -> None:
        self.interrupted = False

    def interrupt(self) -> None:
        self.interrupted = True

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        del params
        if sql == "slow":
            end = time.monotonic() + 5
            while time.monotonic() < end:
                if self.interrupted:
                    raise sqlite3.OperationalError("interrupted")
                time.sleep(0.01)
            return _Cursor()
        return _Cursor()


@pytest.mark.offline
@pytest.mark.asyncio
async def test_deadline_interrupts_slow_read_so_fast_read_proceeds() -> None:
    """A pinned worker is interrupted; the next query is not stuck behind it."""
    store = EventStore(":memory:")
    await store.open()
    conn = _Conn()
    store._read_deadline_s = 0.4
    store._read_executor = ThreadPoolExecutor(max_workers=1)
    store._reader_connection = lambda: conn  # type: ignore[method-assign]

    async def run_slow() -> None:
        with pytest.raises(EventStoreReadDeadlineError):
            await store.query("slow")

    async def run_fast() -> list[dict[str, Any]]:
        await asyncio.sleep(0.3)
        return await store.query("fast")

    try:
        _slow, fast = await asyncio.gather(run_slow(), run_fast())
    finally:
        await store.close()

    assert fast == [{"value": 1}]
    assert conn.interrupted
