"""Cursor subscribe display derivation and DISPLAY_MISMATCH."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db
from agent_bus_store.server import create_app
from fastapi.testclient import TestClient

_TID = "7689373a-2222-4222-8222-222222222222"


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def _thread(slug: str) -> str:
    detail = create_thread(thread_id=None, slug=slug)
    assert detail is not None
    return str(detail["id"])


def test_cursor_subscribe_omitted_display_derived(client: TestClient) -> None:
    thread = _thread("cursor-derived-display")
    placement = {
        "subscription_display": "orion-node",
        "basis": "vscdb",
        "gui_host": "orion-node",
    }
    with patch(
        "bus_watch.cursor_tab_whereami.resolve_cursor_tab_whereami",
        return_value=placement,
    ):
        resp = client.post(
            "/endpoint-subscriptions",
            json={
                "agent": "cursor",
                "thread_scope": thread,
                "surface": "cursor",
                "title": f"{thread} ",
                "agent_id": _TID,
            },
        )
    assert resp.status_code == 200
    assert resp.json()["row"]["display"] == "orion-node"


def test_cursor_subscribe_display_mismatch_422(client: TestClient) -> None:
    thread = _thread("cursor-mismatch")
    placement = {
        "subscription_display": "orion-node",
        "basis": "vscdb",
        "gui_host": "orion-node",
    }
    with patch(
        "bus_watch.cursor_tab_whereami.resolve_cursor_tab_whereami",
        return_value=placement,
    ):
        resp = client.post(
            "/endpoint-subscriptions",
            json={
                "agent": "cursor",
                "thread_scope": thread,
                "surface": "cursor",
                "display": "8",
                "title": f"{thread} ",
                "agent_id": _TID,
            },
        )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "display_mismatch"
    assert detail["resolved_display"] == "orion-node"
    assert detail["supplied_display"] == "8"
