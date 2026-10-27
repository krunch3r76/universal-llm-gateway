"""Mechanical-land HTTP route tests (§13 stage 3)."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.mechanical_land.store import (
    BirthResult,
    MechanicalLandStore,
    MechanicalLandStoreError,
)
from services.git_integration_worker.routes import mechanical_land as ml_routes

pytestmark = pytest.mark.offline

_PLAN = {
    "admitted": True,
    "kind": "implement",
    "source_ref": "todo:mechanical-land-pipeline-s3",
    "policy": {"models": {"implement": {"model": "cursor/composer-2.5", "knobs": {}}}},
    "policy_sha256": "pol" + "0" * 61,
    "dispatch_thread_id": "15851",
    "caller_agent": "grok-bot-gc1",
}


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex"))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_post_birth_happy_path(client: TestClient) -> None:
    ok = BirthResult(
        run_id="ml-testrun0001",
        lane_thread_id="8801",
        dispatch_id="disp-1",
        execution_id="exec-1",
        status="awaiting_leg",
        admit_ok=True,
        http_status=201,
    )
    with patch.object(MechanicalLandStore, "birth_from_plan", return_value=ok):
        resp = client.post("/api/v1/mechanical-land/runs", json=_PLAN)
    assert resp.status_code == 201
    data = resp.json()
    assert data["run_id"] == "ml-testrun0001"
    assert "journal" in data


def test_post_stargate_failure_502_with_run_id(client: TestClient) -> None:
    bad = BirthResult(
        run_id="ml-failedrun01",
        lane_thread_id="8802",
        dispatch_id=None,
        execution_id=None,
        status="blocked",
        admit_ok=False,
        http_status=502,
    )
    with patch.object(MechanicalLandStore, "birth_from_plan", return_value=bad):
        resp = client.post("/api/v1/mechanical-land/runs", json=_PLAN)
    assert resp.status_code == 502
    assert resp.json()["detail"]["run_id"] == "ml-failedrun01"


def test_post_lane_thread_unavailable_503(client: TestClient) -> None:
    with patch.object(
        MechanicalLandStore,
        "birth_from_plan",
        side_effect=MechanicalLandStoreError("lane_thread_unavailable"),
    ):
        resp = client.post("/api/v1/mechanical-land/runs", json=_PLAN)
    assert resp.status_code == 503
    assert resp.json()["detail"]["reason_code"] == "lane_thread_unavailable"


def test_get_run_returns_state_and_events(client: TestClient) -> None:
    snapshot = {
        "run_id": "ml-getrun000001",
        "state": {"run_id": "ml-getrun000001", "status": "awaiting_leg", "tip_seq": 3},
        "events": [{"seq": 1, "event": "run_birthed", "payload": {}}],
    }
    with patch.object(MechanicalLandStore, "get_run", return_value=snapshot):
        resp = client.get("/api/v1/mechanical-land/runs/ml-getrun000001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"]["status"] == "awaiting_leg"
    assert len(body["events"]) == 1


def test_route_handlers_birth_async_get_sync() -> None:
    assert inspect.iscoroutinefunction(ml_routes.create_mechanical_land_run) is True
    assert inspect.iscoroutinefunction(ml_routes.get_mechanical_land_run) is False


def test_post_stargate_connect_error_503(client: TestClient) -> None:
    store = MechanicalLandStore()
    with (
        patch.object(ml_routes, "_store", lambda: store),
        patch.object(store, "_mint_lane_thread", return_value="8810"),
        patch.object(
            store,
            "_post_admit",
            side_effect=httpx.ConnectError("down"),
        ),
    ):
        resp = client.post("/api/v1/mechanical-land/runs", json=_PLAN)
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["error_class"] == "ConnectError"
    assert detail["run_id"]


def test_post_create_thread_raises_503_not_birthing(client: TestClient) -> None:
    store = MechanicalLandStore()

    def _boom(**_kwargs: object) -> str:
        raise RuntimeError("thread db locked")

    with (
        patch.object(ml_routes, "_store", lambda: store),
        patch.object(store, "_mint_lane_thread", side_effect=_boom),
        patch.object(store, "_post_admit") as post,
    ):
        resp = client.post("/api/v1/mechanical-land/runs", json=_PLAN)
        post.assert_not_called()
    assert resp.status_code == 503
    assert resp.json()["detail"]["reason_code"] == "lane_thread_unavailable"
    with store._connect() as conn:
        row = conn.execute(
            "SELECT status FROM mechanical_land_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row["status"] == "blocked"


def test_get_run_many_events_via_http(client: TestClient) -> None:
    store = MechanicalLandStore()
    with (
        patch.object(ml_routes, "_store", lambda: store),
        patch.object(store, "_mint_lane_thread", return_value="8811"),
        patch.object(
            store,
            "_post_admit",
            return_value=(200, {"dispatch_id": "d", "execution_id": "e"}),
        ),
    ):
        run_id = store.birth_from_plan(dict(_PLAN)).run_id
    seq = 3
    for _ in range(22):
        store.append_event(
            run_id=run_id,
            prev_seq=seq,
            step_id="run",
            attempt=1,
            flow="main",
            event="transition_decided",
            payload={
                "from_step": "run",
                "to_step": "run",
                "attempt": 1,
                "reason": "noop",
            },
            source="reactor",
        )
        seq += 1
    resp = client.get(f"/api/v1/mechanical-land/runs/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"]["tip_seq"] == 25
    assert len(body["events"]) == 20


def test_openapi_lists_mechanical_land_get_route(client: TestClient) -> None:
    schema = create_app().openapi()
    paths = schema.get("paths") or {}
    route_key = "/api/v1/mechanical-land/runs/{run_id}"
    assert route_key in paths
    assert "get" in paths[route_key]
