"""Regression tests for the /v1/query raw SQL path.

Thread 631 reported that `observability(raw_sql, ...)` with a typo'd column
name (`ts` instead of `ts_unix_ms`) returned `{rows: [], count: 0}` rather
than a visible error. The silent-empty failure mode made the escape hatch
look like "no data" instead of "query broken". These tests pin the contract:

1. Valid SQL still returns rows.
2. Invalid SQL returns 400 with a meaningful error (not empty rows).
3. Named/structured query paths are unaffected (lenient default preserved).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from event_store.query import create_query_router
from event_store.store import EventStore


@pytest.fixture
def client() -> TestClient:
    store = EventStore(":memory:")

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
        # Opening inside lifespan ensures the sqlite3 connection is created in
        # the same thread that will service requests (TestClient uses a
        # portal thread for async routes).
        await store.open()
        await store.insert_events(
            [
                {
                    "signal": "test.signal.one",
                    "role": "observation",
                    "scope": "global",
                    "ts_unix_ms": 1000,
                    "timestamp": "2026-01-01T00:00:01Z",
                    "source": "test",
                    "payload": {"request_id": "r1"},
                },
                {
                    "signal": "test.signal.two",
                    "role": "observation",
                    "scope": "global",
                    "ts_unix_ms": 2000,
                    "timestamp": "2026-01-01T00:00:02Z",
                    "source": "test",
                    "payload": {"request_id": "r2"},
                },
            ]
        )
        yield
        await store.close()

    class _StubIngest:
        def get_metrics(self) -> dict[str, int]:
            return {}

    app = FastAPI(lifespan=_lifespan)
    app.include_router(create_query_router(store, _StubIngest(), set()))  # type: ignore[arg-type]
    with TestClient(app) as c:
        yield c


def test_raw_sql_valid_returns_rows(client: TestClient) -> None:
    resp = client.post(
        "/v1/query",
        json={
            "type": "sql",
            "sql": "SELECT signal, ts_unix_ms FROM events ORDER BY ts_unix_ms DESC",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "result"
    assert body["count"] == 2
    assert body["rows"][0]["signal"] == "test.signal.two"


def test_raw_sql_bad_column_returns_400(client: TestClient) -> None:
    # Regression: thread 631 — `ts` is NOT a column of `events`; previously
    # this returned {rows: [], count: 0} silently. It MUST now be a 400 with
    # the sqlite error message so the caller can correct the query.
    resp = client.post(
        "/v1/query",
        json={
            "type": "sql",
            "sql": "SELECT seq, signal, ts FROM events ORDER BY ts DESC LIMIT 3",
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body
    assert "no such column" in body["error"].lower()
    assert "ts" in body["error"]


def test_raw_sql_non_select_is_blocked(client: TestClient) -> None:
    resp = client.post(
        "/v1/query",
        json={"type": "sql", "sql": "DELETE FROM events"},
    )
    assert resp.status_code == 403


def test_structured_query_still_returns_rows(client: TestClient) -> None:
    # The lenient store.query() default must remain unchanged for named and
    # structured paths that rely on hard-coded SQL.
    resp = client.post(
        "/v1/query",
        json={"type": "query", "filter": {"signal": "test.signal.*"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "result"
    assert body["count"] == 2
