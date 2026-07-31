"""AC6/AC7: trigger HTTP routes auth and lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from agent_bus_store.auth import require_token
from fastapi.testclient import TestClient

from services.git_integration_worker.app import create_app


def _app_route_paths(app) -> set[str]:
    return set(app.openapi()["paths"].keys())


def test_create_app_registers_trigger_routes_on_live_route_table() -> None:
    """Smoke: triggers module wired into the running app route table."""
    paths = _app_route_paths(create_app())
    assert "/api/v1/triggers" in paths
    assert "/api/v1/triggers/{trigger_id}" in paths


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex"))
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.setenv("PROJECT_ASK_URL", "http://127.0.0.1:8770")
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def test_schedule_refused_without_project_ask_url(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex"))
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.delenv("PROJECT_ASK_URL", raising=False)
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    tc = TestClient(app)
    resp = tc.post(
        "/api/v1/triggers",
        json={
            "delay_s": 60,
            "prompt_text": "hello",
        },
    )
    assert resp.status_code == 503


def test_schedule_refused_without_prompt(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/triggers",
        json={"delay_s": 60},
    )
    assert resp.status_code == 422


def test_auth_missing_token_returns_401(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_BUS_TOKEN", "secret")
    monkeypatch.setenv("PROJECT_ASK_URL", "http://127.0.0.1:8770")
    app = create_app()
    tc = TestClient(app)
    resp = tc.get("/api/v1/triggers")
    assert resp.status_code in (401, 403)


def test_lifecycle_routes(client: TestClient) -> None:
    fire_at = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    with patch(
        "services.git_integration_worker.routes.triggers.publish_lib_signal",
    ):
        created = client.post(
            "/api/v1/triggers",
            json={
                "fire_at": fire_at,
                "prompt_text": "mission follow-up",
                "so_what": "dogfood slice 1",
            },
        )
    assert created.status_code == 200
    body = created.json()
    trigger_id = body["id"]
    assert body["prompt_uri"].startswith("cortex://")

    listed = client.get("/api/v1/triggers")
    assert listed.status_code == 200
    assert listed.json()["count"] >= 1

    got = client.get(f"/api/v1/triggers/{trigger_id}")
    assert got.status_code == 200
    assert got.json()["id"] == trigger_id

    cancelled = client.delete(f"/api/v1/triggers/{trigger_id}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
