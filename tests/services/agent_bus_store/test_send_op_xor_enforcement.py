"""Guardrail D — send op XOR enforcement and path-specific validation (AC-1..AC-8)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from fastapi.testclient import TestClient

MCP_ROOT = Path(__file__).resolve().parents[3] / "services" / "mcp-server"
sys.path.insert(0, str(MCP_ROOT))

from tools import agent_bus as agent_bus_module  # noqa: E402
from tools._agent_bus_post_guard import structured_slug_exists  # noqa: E402


def _app(tmp_path):
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app


def _send_payload(**overrides):
    base = {
        "from": "cursor",
        "to": "web",
        "subject": "test",
        "body": "body",
    }
    base.update(overrides)
    return base


def _create_thread_with_slug(client: TestClient, slug: str) -> str:
    resp = client.post(
        "/threads/send",
        json=_send_payload(new_slug=slug),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["thread"]["id"]


# ── Integration: REST POST /threads/send ─────────────────────────────


def test_ac1_send_new_slug_creates_thread(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        resp = client.post(
            "/threads/send",
            json=_send_payload(new_slug="foo-ac1"),
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["send_path"] == "new_thread"
        assert data["thread"]["slug"] == "foo-ac1"
        assert data["turn"]["turn_number"] == 1


def test_ac2_send_new_slug_collision_returns_409(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        existing_id = _create_thread_with_slug(client, "foo")
        resp = client.post(
            "/threads/send",
            json=_send_payload(new_slug="foo"),
        )
        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "slug_exists"
        assert detail["slug"] == "foo"
        assert detail["existing_thread_id"] == existing_id


def test_ac3_send_thread_continues_thread(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        thread_id = _create_thread_with_slug(client, "continue-ac3")
        resp = client.post(
            "/threads/send",
            json=_send_payload(thread=thread_id, subject="turn-2"),
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["send_path"] == "continue"
        assert data["thread"]["id"] == thread_id
        assert data["turn"]["turn_number"] == 2


def test_ac4_send_both_thread_and_new_slug_returns_400(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        resp = client.post(
            "/threads/send",
            json=_send_payload(new_slug="foo", thread="123"),
        )
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert detail["reason"] == "send_xor_violation"
        assert detail["provided"] == ["thread", "new_slug"]


def test_ac5_send_neither_thread_nor_new_slug_returns_400(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        resp = client.post("/threads/send", json=_send_payload())
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert detail["reason"] == "send_xor_violation"
        assert detail["provided"] == []


def test_ac6_send_continue_enforces_unread_concurrency(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        thread_id = _create_thread_with_slug(client, "unread-ac6")
        # Second turn to web — still unread for web.
        resp2 = client.post(
            "/threads/send",
            json=_send_payload(
                thread=thread_id,
                subject="turn-2",
            ),
        )
        assert resp2.status_code == 201, resp2.text
        # web cannot post until unread turn 2 is read (after_turn=1 blocks on turn 2).
        resp = client.post(
            "/threads/send",
            json={
                **_send_payload(
                    thread=thread_id,
                    subject="blocked-reply",
                ),
                "from": "web",
                "to": "cursor",
                "after_turn": 1,
            },
        )
        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "unread_turns_exist"


def test_ac7_send_new_slug_rejects_after_turn(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        resp = client.post(
            "/threads/send",
            json=_send_payload(new_slug="foo", after_turn=2),
        )
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert detail["reason"] == "after_turn_not_valid_on_new_thread"


def test_ac8_send_continue_rejects_lifecycle_state(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        thread_id = _create_thread_with_slug(client, "lifecycle-ac8")
        resp = client.post(
            "/threads/send",
            json=_send_payload(thread=thread_id, lifecycle_state="pending"),
        )
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert detail["reason"] == "lifecycle_state_not_valid_on_continue"


# ── Unit: MCP dispatch XOR + slug_exists shaping ────────────────────


def test_mcp_send_dispatch_xor_both() -> None:
    with patch.object(agent_bus_module, "record", lambda *_a, **_k: None):
        result = agent_bus_module._send_dispatch(
            new_slug="foo",
            thread="123",
            to="web",
            subject="s",
            body="b",
            from_agent="cursor",
        )
    assert result["reason"] == "send_xor_violation"
    assert result["provided"] == ["thread", "new_slug"]


def test_mcp_send_dispatch_xor_neither() -> None:
    with patch.object(agent_bus_module, "record", lambda *_a, **_k: None):
        result = agent_bus_module._send_dispatch(
            to="web",
            subject="s",
            body="b",
            from_agent="cursor",
        )
    assert result["reason"] == "send_xor_violation"
    assert result["provided"] == []


def test_structured_slug_exists_surfaces_actionable_envelope() -> None:
    relay = {
        "error": "HTTP 409",
        "status_code": 409,
        "detail": {
            "error": "slug_exists",
            "slug": "foo",
            "existing_thread_id": "042",
            "message": "collision",
        },
    }
    envelope = structured_slug_exists(relay)
    assert envelope is not None
    assert envelope["reason"] == "slug_exists"
    assert envelope["existing_thread_id"] == "042"
    assert "send(thread=" in envelope["error"]
