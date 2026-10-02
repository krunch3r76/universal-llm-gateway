"""Query-path health and lock_wait client contract tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from event_store.errors import EventStoreBusyError
from event_store.query import create_query_router
from event_store.query_client_errors import ERROR_CLASS_LOCK_WAIT
from event_store.query_path_health import record_query_completed, snapshot
from event_store.store import EventStore


@pytest.fixture
def client() -> TestClient:
    store = EventStore(":memory:")

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
        await store.open()
        await store.insert_events(
            [
                {
                    "signal": "test.signal",
                    "role": "observation",
                    "scope": "global",
                    "ts_unix_ms": 1000,
                    "timestamp": "2026-01-01T00:00:01Z",
                    "source": "test",
                    "payload": {},
                }
            ]
        )
        yield
        await store.close()

    class _StubIngest:
        def get_metrics(self) -> dict[str, int]:
            return {"subscriber_overflow_pending": 3}

    app = FastAPI(lifespan=_lifespan)
    app.include_router(create_query_router(store, _StubIngest(), set()))  # type: ignore[arg-type]
    with TestClient(app) as c:
        yield c


def test_health_includes_query_path(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "query_path" in body
    assert "query_completed_age_ms" in body["query_path"]
    assert "seconds_since_last_query" not in body["query_path"]
    assert body["subscriber_overflow_pending"] == 3


def test_query_updates_query_completed_age_ms(client: TestClient) -> None:
    before = client.get("/health").json()["query_path"]["query_completed_age_ms"]
    client.post(
        "/v1/query",
        json={"type": "sql", "sql": "SELECT signal FROM events LIMIT 1"},
    )
    after = client.get("/health").json()["query_path"]
    assert after["query_completed_age_ms"] is not None
    assert after["last_query_duration_s"] is not None
    assert before is None or after["query_completed_age_ms"] <= 5000.0


def test_raw_sql_lock_wait_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _busy(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise EventStoreBusyError("database is locked")

    monkeypatch.setattr(EventStore, "query", _busy)

    resp = client.post(
        "/v1/query",
        json={"type": "sql", "sql": "SELECT 1"},
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["error_class"] == ERROR_CLASS_LOCK_WAIT


def test_operation_lock_wait_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "event_store.query.execute_operation",
        AsyncMock(
            return_value={
                "error": "Event store waited on a database lock: database is locked",
                "error_class": ERROR_CLASS_LOCK_WAIT,
            }
        ),
    )
    resp = client.post(
        "/v1/query",
        json={"type": "operation", "name": "stack-last-started", "params": {}},
    )
    assert resp.status_code == 503
    assert resp.json()["error_class"] == ERROR_CLASS_LOCK_WAIT


def test_record_query_completed_snapshot() -> None:
    record_query_completed(0.01)
    snap = snapshot()
    assert snap["last_query_duration_s"] == 0.01
    assert snap["query_completed_age_ms"] is not None
