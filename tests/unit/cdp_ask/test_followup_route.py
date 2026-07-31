"""Route smoke tests for POST /v1/project-ask/followups."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from cdp_ask.app import create_app
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.models import FollowupProjectAskResponse
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline


def test_followup_route_no_execution_store_create(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setattr("cdp_ask.app.verify_harvest_root", lambda: tmp_path)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.list_active",
        lambda: [],
    )
    store = ExecutionStore()
    create_called = False
    original_create = store.create

    async def _wrapped_create(**kwargs: Any) -> Any:
        nonlocal create_called
        create_called = True
        return await original_create(**kwargs)

    store.create = _wrapped_create  # type: ignore[method-assign]

    expected = FollowupProjectAskResponse(ok=False, error="lane_not_attached")
    monkeypatch.setattr(
        "cdp_ask.app.execute_followup",
        AsyncMock(return_value=expected),
    )
    app = create_app(store=store)
    with TestClient(app) as client:
        resp = client.post(
            "/v1/project-ask/followups",
            json={
                "registration_id": "reg-1",
                "prompt_text": "hello",
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "lane_not_attached"
    assert create_called is False


def test_followup_route_response_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setattr("cdp_ask.app.verify_harvest_root", lambda: tmp_path)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.list_active",
        lambda: [],
    )
    expected = FollowupProjectAskResponse(
        ok=True,
        url="https://claude.ai/cowork/cse_x",
        registration_id="reg-1",
        send_verified=True,
        streaming_at_paste=False,
        pasted_at=123.0,
    )
    monkeypatch.setattr(
        "cdp_ask.app.execute_followup",
        AsyncMock(return_value=expected),
    )
    app = create_app(store=ExecutionStore())
    with TestClient(app) as client:
        resp = client.post(
            "/v1/project-ask/followups",
            json={
                "registration_id": "reg-1",
                "prompt_text": "hello",
            },
        )
    data = resp.json()
    assert data["ok"] is True
    assert data["url"] == "https://claude.ai/cowork/cse_x"
    assert data["send_verified"] is True
    assert "registration_id" in data
