"""L2 — maestro ↔ web-anthropic share one read pointer on a thread."""

from __future__ import annotations

from agent_bus_store.auth import require_token
from agent_bus_store.server import create_app
from fastapi.testclient import TestClient


def _client(tmp_path) -> TestClient:
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def test_maestro_mark_read_clears_web_anthropic_inbox(tmp_path) -> None:
    with _client(tmp_path) as client:
        thread_id = client.post(
            "/threads/with-turn",
            json={
                "slug": "l2-maestro-clears-web",
                "from": "cursor",
                "to": "web-anthropic",
                "subject": "REQUEST|x",
                "body": "unread",
            },
        ).json()["thread"]["id"]
        mark = client.patch(
            f"/threads/{thread_id}/turns/read-state",
            json={"through_turn": 1, "agent": "maestro"},
        )
        assert mark.status_code == 200, mark.text
        assert mark.json()["marked_read"] == 1
        turn = client.get(f"/turns/by-number?thread={thread_id}&turn_number=1").json()
        assert turn["read_at"] is not None
        send = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "web-anthropic",
                "to": "cursor",
                "subject": "ACK|x",
                "body": "ok",
                "after_turn": 0,
            },
        )
        assert send.status_code == 201, send.text


def test_web_anthropic_mark_read_clears_maestro_inbox(tmp_path) -> None:
    with _client(tmp_path) as client:
        thread_id = client.post(
            "/threads/with-turn",
            json={
                "slug": "l2-web-clears-maestro",
                "from": "cursor",
                "to": "maestro",
                "subject": "REQUEST|y",
                "body": "unread",
            },
        ).json()["thread"]["id"]
        mark = client.patch(
            f"/threads/{thread_id}/turns/read-state",
            json={"through_turn": 1, "agent": "web-anthropic"},
        )
        assert mark.status_code == 200, mark.text
        assert mark.json()["marked_read"] == 1
        send = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "maestro",
                "to": "cursor",
                "subject": "ACK|y",
                "body": "ok",
                "after_turn": 0,
            },
        )
        assert send.status_code == 201, send.text


def test_maestro_mark_read_does_not_clear_cursor_or_grok_inbox(tmp_path) -> None:
    with _client(tmp_path) as client:
        thread_id = client.post(
            "/threads/with-turn",
            json={
                "slug": "l2-isolation",
                "from": "cursor",
                "to": "cursor",
                "subject": "inbox-cursor",
                "body": "u1",
            },
        ).json()["thread"]["id"]
        client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "grok-bot-gc1",
                "subject": "REQUEST|g",
                "body": "u2",
            },
        )
        client.patch(
            f"/threads/{thread_id}/turns/read-state",
            json={"through_turn": 2, "agent": "maestro"},
        )
        t1 = client.get(f"/turns/by-number?thread={thread_id}&turn_number=1").json()
        t2 = client.get(f"/turns/by-number?thread={thread_id}&turn_number=2").json()
        assert t1["read_at"] is None
        assert t2["read_at"] is None
