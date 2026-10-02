"""Acceptance tests for query-path diagnosability (spec S4b items 1–6, 8 on HEAD)."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from event_store.errors import EventStoreBusyError
from event_store.query import create_query_router
from event_store.query_client_errors import ERROR_CLASS_CLIENT_DEADLINE, ERROR_CLASS_LOCK_WAIT
from event_store.query_path_health import (
    run_event_loop_lag_probe,
    set_event_loop_lag_ms,
    snapshot,
)
from event_store.server import create_app
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
        lag_task = asyncio.create_task(run_event_loop_lag_probe())
        _app.state.event_loop_lag_probe_task = lag_task
        try:
            yield
        finally:
            lag_task.cancel()
            try:
                await lag_task
            except asyncio.CancelledError:
                pass
            await store.close()

    class _StubIngest:
        def get_metrics(self) -> dict[str, int]:
            return {"subscriber_overflow_pending": 0}

    app = FastAPI(lifespan=_lifespan)
    app.include_router(create_query_router(store, _StubIngest(), set()))  # type: ignore[arg-type]
    with TestClient(app) as c:
        yield c


# AC1 — /health stays 200 with identity + query_path progress; lag unset without probe.
def test_ac1_health_includes_query_path_and_identity(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "pid" in body
    assert "code_version" in body
    qp = body["query_path"]
    assert "event_loop_lag_ms" in qp
    assert "query_completed_age_ms" in qp
    assert "seconds_since_last_query" not in qp
    assert "last_query_duration_s" in qp


def test_ac1_health_does_not_touch_store_on_get(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("health must not run SQL")

    monkeypatch.setattr(EventStore, "query", _boom)
    resp = client.get("/health")
    assert resp.status_code == 200


# AC2 — lag heartbeat starts from create_app lifespan (task ref on app.state).
def test_ac2_lag_sample_readable_via_setter() -> None:
    set_event_loop_lag_ms(25.0)
    assert snapshot()["event_loop_lag_ms"] == 25.0


@pytest.mark.asyncio
async def test_ac2_create_app_lifespan_starts_lag_probe() -> None:
    store = EventStore(":memory:")
    await store.open()

    class _StubIngest:
        def get_metrics(self) -> dict[str, int]:
            return {}

    app = create_app(store, set(), _StubIngest())  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        assert getattr(app.state, "event_loop_lag_probe_task", None) is not None
        await asyncio.sleep(0.08)
        assert snapshot()["event_loop_lag_ms"] is not None
    await store.close()


# AC3 — finished /v1/query stamps completion age; 503 lock-wait included; no query yet unset.
def test_ac3_no_query_leaves_completion_age_unset(client: TestClient) -> None:
    qp = client.get("/health").json()["query_path"]
    assert qp["query_completed_age_ms"] is None
    assert qp["last_query_duration_s"] is None


def test_ac3_finished_query_stamps_completion_age(client: TestClient) -> None:
    client.post(
        "/v1/query",
        json={"type": "sql", "sql": "SELECT signal FROM events LIMIT 1"},
    )
    qp = client.get("/health").json()["query_path"]
    assert qp["query_completed_age_ms"] is not None
    assert qp["last_query_duration_s"] is not None
    assert qp["query_completed_age_ms"] <= 2000.0


def test_ac3_lock_wait_503_stamps_completion_age(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _busy(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise EventStoreBusyError("database is locked")

    monkeypatch.setattr(EventStore, "query", _busy)
    resp = client.post(
        "/v1/query",
        json={"type": "sql", "sql": "SELECT 1"},
    )
    assert resp.status_code == 503
    qp = client.get("/health").json()["query_path"]
    assert qp["query_completed_age_ms"] is not None


# AC4 — lenient store.query swallows non-busy sqlite errors; busy re-raises.
@pytest.mark.asyncio
async def test_ac4_lenient_swallows_non_busy_sqlite_error() -> None:
    store = EventStore(":memory:")
    await store.open()

    class _Conn:
        def execute(self, *_a, **_k):  # type: ignore[no-untyped-def]
            raise sqlite3.OperationalError("no such table: events_typo")

    store._reader_connection = lambda: _Conn()  # type: ignore[method-assign]
    rows = await store.query("SELECT 1", raise_on_error=False)
    assert rows == []


@pytest.mark.asyncio
async def test_ac4_lenient_reraises_sqlite_busy() -> None:
    store = EventStore(":memory:")
    await store.open()

    class _Conn:
        def execute(self, *_a, **_k):  # type: ignore[no-untyped-def]
            raise sqlite3.OperationalError("database is locked")

    store._reader_connection = lambda: _Conn()  # type: ignore[method-assign]
    with pytest.raises(EventStoreBusyError):
        await store.query("SELECT 1", raise_on_error=False)


# AC5 — named operation busy via dispatch → store → 503 + error_class lock_wait.
def test_ac5_named_operation_busy_via_store_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _busy(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise EventStoreBusyError("database is locked")

    monkeypatch.setattr(EventStore, "query", _busy)
    resp = client.post(
        "/v1/query",
        json={"type": "operation", "name": "stack-last-started", "params": {}},
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["error_class"] == ERROR_CLASS_LOCK_WAIT
    assert "wait" in body["error"].lower()


def test_ac5_named_operation_non_busy_sqlite_stays_lenient(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "event_store.query.execute_operation",
        AsyncMock(return_value={"rows": [], "count": 0}),
    )
    resp = client.post(
        "/v1/query",
        json={"type": "operation", "name": "stack-last-started", "params": {}},
    )
    assert resp.status_code == 200
    assert resp.json()["type"] == "result"


# AC6 — busy on structured/raw → 503 lock_wait; syntax stays 400 without lock_wait.
def test_ac6_structured_query_busy_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _busy(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise EventStoreBusyError("database is locked")

    monkeypatch.setattr(EventStore, "query", _busy)
    resp = client.post(
        "/v1/query",
        json={"type": "query", "filter": {"signal": "test.signal"}},
    )
    assert resp.status_code == 503
    assert resp.json()["error_class"] == ERROR_CLASS_LOCK_WAIT


def test_ac6_raw_sql_bad_column_not_busy(client: TestClient) -> None:
    resp = client.post(
        "/v1/query",
        json={"type": "sql", "sql": "SELECT ts FROM events LIMIT 1"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body.get("error_class") != ERROR_CLASS_LOCK_WAIT
    assert "no such column" in body["error"].lower()


def test_ac6_raw_sql_busy_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _busy(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise EventStoreBusyError("database is locked")

    monkeypatch.setattr(EventStore, "query", _busy)
    resp = client.post(
        "/v1/query",
        json={"type": "sql", "sql": "SELECT 1"},
    )
    assert resp.status_code == 503
    assert resp.json()["error_class"] == ERROR_CLASS_LOCK_WAIT


# AC7/8 — client deadline envelope (local route) states 10s deadline; no narrow-query hint.
def test_ac7_client_deadline_envelope_text() -> None:
    from event_store.query_client_errors import envelope_client_deadline

    env = envelope_client_deadline()
    assert env["error_class"] == ERROR_CLASS_CLIENT_DEADLINE
    assert "10s client deadline" in env["error"]
    assert "narrower query" not in env["error"].lower()
    assert "query_completed_age_ms" in env["error"]
