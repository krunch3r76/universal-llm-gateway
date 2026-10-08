"""Server-side read deadline interrupts a slow reader so a fast read can run."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from event_store.errors import EventStoreReadDeadlineError
from event_store.query import create_query_router
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


@pytest.mark.offline
@pytest.mark.asyncio
async def test_late_deadline_does_not_abort_the_next_read() -> None:
    """A stalled loop must not interrupt the read that replaced the timed-out one."""
    store = EventStore(":memory:")
    await store.open()
    release = threading.Event()
    aborted_during_fast = False

    class _Conn:
        def __init__(self) -> None:
            self.current = ""

        def interrupt(self) -> None:
            nonlocal aborted_during_fast
            if self.current == "fast":
                aborted_during_fast = True

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
            del params
            self.current = sql
            if sql == "slow":
                time.sleep(0.2)
                return _Cursor()
            release.wait(timeout=2)
            if aborted_during_fast:
                raise sqlite3.OperationalError("interrupted")
            return _Cursor()

    conn = _Conn()
    store._read_deadline_s = 0.05
    store._read_queue_deadline_s = 2
    store._read_executor = ThreadPoolExecutor(max_workers=1)
    store._reader_connection = lambda: conn  # type: ignore[method-assign]

    slow = asyncio.create_task(store.query("slow"))
    await asyncio.sleep(0.02)
    fast = asyncio.create_task(store.query("fast"))
    await asyncio.sleep(0)
    time.sleep(0.35)
    await asyncio.sleep(0)
    release.set()
    try:
        _slow_rows, fast_rows = await asyncio.gather(slow, fast, return_exceptions=True)
    finally:
        await store.close()

    assert not aborted_during_fast
    assert fast_rows == [{"value": 1}]


def test_raw_sql_deadline_returns_503() -> None:
    """The raw SQL route maps a read deadline to retryable 503."""
    store = EventStore(":memory:")

    class _Stub:
        def get_metrics(self) -> dict[str, int]:
            return {}

    app = FastAPI()
    app.include_router(create_query_router(store, _Stub(), set()))  # type: ignore[arg-type]

    async def boom(*_args: object, **_kwargs: object) -> list[dict[str, Any]]:
        raise EventStoreReadDeadlineError("deadline")

    store.query = boom  # type: ignore[method-assign]
    store._db = object()  # type: ignore[assignment]
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/observability/sql",
            json={"sql": "SELECT 1"},
        )
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "READ_DEADLINE"
    assert body["retryable"] is True


def test_retention_run_route_deletes_once() -> None:
    """POST /api/v1/retention/run is the operator trigger and ignores the gate."""
    store = EventStore(":memory:")

    class _Stub:
        def get_metrics(self) -> dict[str, int]:
            return {}

    calls = {"n": 0}

    async def runner() -> dict[str, int]:
        calls["n"] += 1
        return {"debug": 0, "heartbeat": 0, "age": 3, "session": 0}

    app = FastAPI()
    app.include_router(
        create_query_router(store, _Stub(), set(), retention_runner=runner)  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/retention/run")
    assert response.status_code == 200
    assert response.json()["age"] == 3
    assert response.json()["dry_run"] is False
    assert calls["n"] == 1
