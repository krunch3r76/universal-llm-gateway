"""Origin observability routes."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from event_store.errors import EventStoreBusyError
from event_store.operation_catalog import list_operations
from event_store.query import create_query_router
from event_store.store import EventStore


@pytest.fixture
def client() -> TestClient:
    store = EventStore(":memory:")

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
        await store.open()
        yield

    class _StubIngest:
        def get_metrics(self) -> dict[str, int]:
            return {}

    app = FastAPI(lifespan=_lifespan)
    app.include_router(create_query_router(store, _StubIngest(), set()))  # type: ignore[arg-type]
    with TestClient(app) as test_client:
        yield test_client


def test_listing_has_27_members(client: TestClient) -> None:
    resp = client.get("/api/v1/observability")
    assert resp.status_code == 200
    names = {row["name"] for row in resp.json()["members"]}
    assert names == {row["name"] for row in list_operations()}
    assert len(names) == 27


def test_pipeline_trace_and_noise_profile(client: TestClient) -> None:
    traced = client.get(
        "/api/v1/observability/pipeline-trace", params={"execution_id": "x"}
    )
    assert traced.status_code == 200
    assert traced.json()["operation"] == "pipeline-trace"
    noise = client.get("/api/v1/observability/noise-profile", params={"minutes": "5"})
    assert noise.status_code == 200
    assert noise.json()["minutes"] == 5


def test_unknown_and_missing_and_bad_int(client: TestClient) -> None:
    unknown = client.get("/api/v1/observability/noise-profile", params={"minuts": "5"})
    assert unknown.status_code == 400
    body = unknown.json()
    assert body["code"] == "INVALID_PARAMS"
    assert body["data"]["unknown"] == ["minuts"]
    assert "minutes" in body["data"]["declared"]
    missing = client.get("/api/v1/observability/model-timeline")
    assert missing.status_code == 400
    assert missing.json()["data"]["missing"] == ["model_id"]
    bad = client.get("/api/v1/observability/recent-failures", params={"limit": "abc"})
    assert bad.status_code == 400


def test_unknown_member_and_delivery_oneof_and_sql(client: TestClient) -> None:
    missing = client.get("/api/v1/observability/no-such-op")
    assert missing.status_code == 404
    assert missing.json()["code"] == "UNKNOWN_MEMBER"
    assert missing.json()["data"]["members"]
    rejected = client.get(
        "/api/v1/observability/delivery-audit-parent",
        params={"audit_id": "a", "request_id": "b"},
    )
    assert rejected.status_code == 422
    ok = client.post("/api/v1/observability/sql", json={"sql": "SELECT 1 AS x"})
    assert ok.status_code == 200
    assert ok.json()["rows"] == [{"x": 1}]
    forbidden = client.post(
        "/api/v1/observability/sql", json={"sql": "DELETE FROM events"}
    )
    assert forbidden.status_code == 403
    malformed = client.post(
        "/api/v1/observability/sql", json={"sql": "SELECT no_such_fn("}
    )
    assert malformed.status_code == 400
    assert malformed.json()["code"] == "SQL_ERROR"
    method = client.get("/api/v1/observability/sql")
    assert method.status_code == 405


def test_lock_wait_and_removed_route(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _busy(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise EventStoreBusyError("database is locked")

    monkeypatch.setattr(EventStore, "query", _busy)
    busy = client.post("/api/v1/observability/sql", json={"sql": "SELECT 1"})
    assert busy.status_code == 503
    assert busy.json()["code"] == "LOCK_WAIT"
    assert busy.json()["retryable"] is True
    gone = client.post("/v1/query", json={"type": "operations"})
    assert gone.status_code == 404
