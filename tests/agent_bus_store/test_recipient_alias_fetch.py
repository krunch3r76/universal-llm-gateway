"""Unread inbox fetches must match legacy short to_agent slugs (web, cursor)."""

from __future__ import annotations

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from fastapi.testclient import TestClient


def _app(tmp_path):
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app


def test_unread_fetch_claude_web_matches_legacy_web_to_agent(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        created = client.post(
            "/threads/with-turn",
            json={
                "slug": "alias-inbox-test",
                "from": "claude-cursor",
                "to": "web",
                "subject": "for web lead",
                "body": "body",
            },
        )
        assert created.status_code == 201
        thread_id = created.json()["thread"]["id"]

        resp = client.get(f"/turns?thread={thread_id}&to=claude-web&unread=true")
        assert resp.status_code == 200
        turns = resp.json()["turns"]
        assert len(turns) == 1
        assert turns[0]["to"] == "web"
        assert turns[0]["read_at"] is None


def test_unread_fetch_cursor_matches_legacy_cursor_to_agent(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        created = client.post(
            "/threads/with-turn",
            json={
                "slug": "alias-cursor-inbox",
                "from": "claude-web",
                "to": "cursor",
                "subject": "for cursor",
                "body": "body",
            },
        )
        assert created.status_code == 201
        thread_id = created.json()["thread"]["id"]

        resp = client.get(f"/turns?thread={thread_id}&to=claude-cursor&unread=true")
        assert resp.status_code == 200
        assert len(resp.json()["turns"]) == 1
