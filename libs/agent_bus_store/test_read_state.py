"""E5 tests: send mark_read fix and bulk read-state."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token


def _app(tmp_path):
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app


def test_send_mark_read_clears_inbox_not_outgoing(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "mr-send",
                "from": "web",
                "to": "cursor",
                "subject": "inbox",
                "body": "unread",
            },
        )
        thread_id = create.json()["thread"]["id"]
        resp = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "reply",
                "body": "clearing",
                "mark_read": True,
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["marked_read"] == 1
        new_turn = data["turn"]["turn_number"]
        head = client.get(
            f"/turns/by-number?thread={thread_id}&turn_number={new_turn}"
        ).json()
        assert head["read_at"] is None
        first = client.get(
            f"/turns/by-number?thread={thread_id}&turn_number=1"
        ).json()
        assert first["read_at"] is not None


def test_send_mark_read_skips_to_all(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "mr-all",
                "from": "cursor",
                "to": "web",
                "subject": "one",
                "body": "b1",
            },
        )
        thread_id = create.json()["thread"]["id"]
        client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "web",
                "to": "all",
                "subject": "broadcast",
                "body": "bc",
            },
        )
        resp = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "reply",
                "body": "no auto all",
                "mark_read": True,
            },
        )
        assert resp.status_code == 201
        assert resp.json()["marked_read"] == 0
        broadcast = client.get(
            f"/turns/by-number?thread={thread_id}&turn_number=2"
        ).json()
        assert broadcast["read_at"] is None


def test_bulk_mark_read_xor_422(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "bulk-xor",
                "from": "cursor",
                "to": "web",
                "subject": "one",
                "body": "b1",
            },
        )
        thread_id = create.json()["thread"]["id"]
        resp = client.patch(
            f"/threads/{thread_id}/turns/read-state",
            json={"turn_numbers": [1], "through_turn": 1, "agent": "cursor"},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "read_state_xor_violation"


def test_bulk_through_turn_requires_agent(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "bulk-agent",
                "from": "cursor",
                "to": "web",
                "subject": "one",
                "body": "b1",
            },
        )
        thread_id = create.json()["thread"]["id"]
        resp = client.patch(
            f"/threads/{thread_id}/turns/read-state",
            json={"through_turn": 1},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "through_turn_requires_agent"


def test_bulk_through_turn_marks_unread_to_agent(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "bulk-through",
                "from": "cursor",
                "to": "web",
                "subject": "one",
                "body": "b1",
            },
        )
        thread_id = create.json()["thread"]["id"]
        client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "web",
                "to": "cursor",
                "subject": "inbox",
                "body": "u",
            },
        )
        resp = client.patch(
            f"/threads/{thread_id}/turns/read-state",
            json={"through_turn": 2, "agent": "cursor"},
        )
        assert resp.status_code == 200
        assert resp.json()["marked_read"] == 1
