"""Offline tests for life_dispatch route admission."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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


def test_life_dispatch_prompt_runs_cdp_generate_path(client: TestClient) -> None:
    """Prompt path executes through dispatch_cdp_generate (not mocked at route)."""
    staged = MagicMock(
        prompt_uri="cortex://notes/system/ephemeral/prompt.md",
        staged=True,
    )

    class _FakeTask:
        def add_done_callback(self, _cb: object) -> None:
            return None

        def cancelled(self) -> bool:
            return False

        def exception(self) -> None:
            return None

    pending: list[object] = []

    def _capture_task(coro: object, **_kwargs: object) -> _FakeTask:
        pending.append(coro)
        return _FakeTask()

    with (
        patch(
            "systems.frontier_consult.life_dispatch_routes.cdp_project_binding",
            return_value="01a05c28-733b-72ee-bba6-c72e81ed6d41",
        ),
        patch(
            "systems.frontier_consult.cdp_generate._stage_inputs",
            return_value=staged,
        ),
        patch(
            "systems.frontier_consult.cdp_generate.create_handoff_thread",
            new=AsyncMock(return_value="9901"),
        ) as mock_create_thread,
        patch(
            "systems.frontier_consult.cdp_generate.upsert_inflight_leg",
            return_value=None,
        ),
        patch(
            "systems.frontier_consult.cdp_generate.emit_poll_hint_from_handoff",
            return_value=None,
        ),
        patch(
            "systems.frontier_consult.cdp_generate.observe_mission_binding",
            return_value=None,
        ),
        patch(
            "systems.frontier_consult.cdp_generate.run_cdp_worker",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "systems.frontier_consult.cdp_generate.asyncio.create_task",
            side_effect=_capture_task,
        ),
    ):
        resp = client.post(
            "/api/v1/life/dispatch",
            json={"prompt": "hello"},
        )

    assert resp.status_code == 202
    payload = resp.json()
    assert payload["status"] == "running"
    assert payload["execution_id"]
    assert payload["thread"] == "9901"
    assert payload["poll_hint"]["arguments"]["thread"] == "9901"
    mock_create_thread.assert_awaited_once()
    assert mock_create_thread.await_args.kwargs["caller_agent"] == "life"
    assert pending
