"""Cursor subscribe display derivation and DISPLAY_MISMATCH."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db
from agent_bus_store.server import create_app
from fastapi.testclient import TestClient

_PLACEMENT_PATCH = (
    "agent_bus_store.db.cursor_subscribe_placement.resolve_cursor_tab_whereami"
)

_TID = "7689373a-2499-41f0-9ff6-fccbcf130362"


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
    with patch(_PLACEMENT_PATCH, return_value=placement):
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
    with patch(_PLACEMENT_PATCH, return_value=placement):
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


def test_cursor_subscribe_inferred_other_panes_accepts_display_eight(
    client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    thread = _thread("cursor-inferred-other")
    db = tmp_path / "state.vscdb"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
        conn.execute(
            "INSERT INTO ItemTable VALUES (?, ?)",
            ("glass.agentConversationPanes.other-id", b"{}"),
        )
    monkeypatch.setenv("CURSOR_VSCDB_PATHS", f"{db}|:8")
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
    assert resp.status_code == 200
    assert resp.json()["row"]["display"] == "8"
    evidence = str(resp.json()["row"].get("evidence") or "")
    assert "placement_basis:vscdb_inferred" in evidence


def test_cursor_subscribe_display_override_emits_event(client: TestClient) -> None:
    thread = _thread("cursor-override")
    placement = {
        "subscription_display": "orion-node",
        "basis": "vscdb",
    }
    with patch(_PLACEMENT_PATCH, return_value=placement):
        with patch(
            "agent_bus_store.routes.threads.subscriptions.emit_endpoint_display_override"
        ) as emit_override:
            with patch(
                "agent_bus_store.routes.threads.subscriptions.emit_endpoint_subscribed"
            ) as emit_subscribed:
                resp = client.post(
                    "/endpoint-subscriptions",
                    json={
                        "agent": "cursor",
                        "thread_scope": thread,
                        "surface": "cursor",
                        "display": "8",
                        "title": f"{thread} ",
                        "agent_id": _TID,
                        "display_override": True,
                        "display_override_reason": "operator confirmed",
                    },
                )
    assert resp.status_code == 200
    emit_override.assert_called_once()
    emit_subscribed.assert_called_once()
    assert emit_override.call_count == 1
    assert emit_subscribed.call_count == 1


def test_display_override_reason_semicolon_encoded_in_evidence(
    client: TestClient,
) -> None:
    thread = _thread("cursor-override-evidence")
    placement = {
        "subscription_display": "orion-node",
        "basis": "vscdb",
    }
    with patch(_PLACEMENT_PATCH, return_value=placement):
        resp = client.post(
            "/endpoint-subscriptions",
            json={
                "agent": "cursor",
                "thread_scope": thread,
                "surface": "cursor",
                "display": "8",
                "title": f"{thread} ",
                "agent_id": _TID,
                "display_override": True,
                "display_override_reason": "a;b",
            },
        )
    assert resp.status_code == 200
    evidence = str(resp.json()["row"].get("evidence") or "")
    assert "display_override:a%3Bb" in evidence
    assert "display_override:a;b" not in evidence
