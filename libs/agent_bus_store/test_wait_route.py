"""HTTP tests for GET /threads/{thread_id}/wait."""

from __future__ import annotations

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from fastapi.testclient import TestClient


def _app(tmp_path):
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app


def test_wait_zero_returns_snapshot_awaiting_first_reply(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        created = client.post(
            "/threads/with-turn",
            json={
                "slug": "wait-snapshot-test",
                "from": "claude-cursor",
                "to": "web",
                "subject": "handoff",
                "body": "brief",
            },
        )
        assert created.status_code == 201
        thread_id = created.json()["thread"]["id"]

        resp = client.get(
            f"/threads/{thread_id}/wait"
            "?after_turn=1&wait=0&completion=first_reply_from&from_agent=claude-web"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "awaiting_first_reply"
        assert body["complete"] is False
        assert body["push_required"] is False
        assert body["thread_id"] == thread_id


def test_wait_complete_after_qualifying_reply(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        created = client.post(
            "/threads/with-turn",
            json={
                "slug": "wait-complete-test",
                "from": "claude-cursor",
                "to": "web",
                "subject": "handoff",
                "body": "brief",
            },
        )
        assert created.status_code == 201
        thread_id = created.json()["thread"]["id"]

        reply = client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "claude-web",
                "to": "cursor",
                "subject": "re: handoff",
                "body": "done",
                "after_turn": 1,
            },
        )
        assert reply.status_code == 201

        resp = client.get(
            f"/threads/{thread_id}/wait"
            "?after_turn=1&wait=0&completion=first_reply_from&from_agent=claude-web"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "complete"
        assert body["complete"] is True
        assert body["qualifying_reply_turn"] == 2
        nudge = body["suggested_next"]
        assert nudge is not None
        assert nudge["phase"] == "consult_turn_posted"
        assert nudge["consult_turn"] == 2
        assert nudge["pointer_turn"] == 1
        assert any(s["action"] == "close_handoff_thread" for s in nudge["steps"])


def test_wait_suggested_next_absent_when_thread_closed(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        created = client.post(
            "/threads/with-turn",
            json={
                "slug": "wait-closed-test",
                "from": "claude-cursor",
                "to": "web",
                "subject": "handoff",
                "body": "brief",
            },
        )
        thread_id = created.json()["thread"]["id"]
        client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "claude-web",
                "to": "cursor",
                "subject": "re",
                "body": "done",
                "after_turn": 1,
            },
        )
        close = client.patch(
            f"/threads/{thread_id}/close",
            json={"summary": "done"},
        )
        assert close.status_code == 200
        resp = client.get(
            f"/threads/{thread_id}/wait"
            "?after_turn=1&wait=0&completion=first_reply_from&from_agent=claude-web"
        )
        body = resp.json()
        assert body["complete"] is True
        assert body["suggested_next"] is None


def test_wait_alias_canonical_hint_legacy_reply(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        created = client.post(
            "/threads/with-turn",
            json={
                "slug": "wait-alias-test",
                "from": "claude-cursor",
                "to": "claude-cursor",
                "subject": "handoff",
                "body": "brief",
            },
        )
        thread_id = created.json()["thread"]["id"]
        client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "claude-cursor",
                "subject": "re",
                "body": "findings",
                "after_turn": 1,
            },
        )
        resp = client.get(
            f"/threads/{thread_id}/wait"
            "?after_turn=1&wait=0&completion=first_reply_from&from_agent=claude-cursor"
        )
        body = resp.json()
        assert body["complete"] is True
        assert body["qualifying_reply_turn"] == 2
