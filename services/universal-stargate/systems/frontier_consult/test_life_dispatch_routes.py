"""Offline tests for life_dispatch route admission."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from systems.frontier_consult.life_dispatch_routes import life_dispatch_router

pytestmark = pytest.mark.offline


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(life_dispatch_router)
    return TestClient(app)


def test_life_dispatch_rejects_empty_body(client: TestClient) -> None:
    resp = client.post("/api/v1/life/dispatch", json={"model": "cdp/opus-5"})
    assert resp.status_code == 422


def test_life_dispatch_rejects_forbidden_extra_field(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/life/dispatch",
        json={"prompt": "hi", "project_uuid": "evil"},
    )
    assert resp.status_code == 422


def test_life_dispatch_prompt_admits(client: TestClient) -> None:
    admit = {
        "op": "generate",
        "status": "running",
        "execution_id": "exec-life-1",
        "thread": "9901",
    }
    with (
        patch(
            "systems.frontier_consult.life_dispatch_routes.cdp_project_binding",
            return_value="01a05c28-733b-72ee-bba6-c72e81ed6d41",
        ),
        patch(
            "systems.frontier_consult.life_dispatch_routes.dispatch_cdp_generate",
            new=AsyncMock(return_value=admit),
        ) as mock_dispatch,
    ):
        resp = client.post(
            "/api/v1/life/dispatch",
            json={"prompt": "hello"},
        )
    assert resp.status_code == 200
    assert resp.json()["execution_id"] == "exec-life-1"
    kwargs = mock_dispatch.await_args.kwargs
    assert kwargs["project_uuid"] == "01a05c28-733b-72ee-bba6-c72e81ed6d41"
    assert kwargs["body"].purpose == "operator-proxy"
