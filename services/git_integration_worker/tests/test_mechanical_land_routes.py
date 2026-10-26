"""Mechanical-land HTTP route tests (§13 stage 3)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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


def test_openapi_lists_mechanical_land_get_route(client: TestClient) -> None:
    schema = create_app().openapi()
    paths = schema.get("paths") or {}
    route_key = "/api/v1/mechanical-land/runs/{run_id}"
    assert route_key in paths
    assert "get" in paths[route_key]
