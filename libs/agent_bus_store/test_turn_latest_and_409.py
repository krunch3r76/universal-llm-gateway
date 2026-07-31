"""E3 tests: get(latest) and 409 after_turn enrichment."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token


def _app(tmp_path):
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app


def test_get_latest_returns_head_turn(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "latest-test",
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
                "subject": "two",
                "body": "b2",
            },
        )
        resp = client.get(f"/turns/by-number?thread={thread_id}&turn_number=latest")
        assert resp.status_code == 200, resp.text
        assert resp.json()["turn_number"] == 2
        assert resp.json()["subject"] == "two"


def test_get_latest_empty_thread_404_turn_count(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        create = client.post(
            "/threads",
            json={"slug": "empty-thread"},
        )
        thread_id = create.json()["id"]
        resp = client.get(f"/turns/by-number?thread={thread_id}&turn_number=latest")
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["data"]["turn_count"] == 0


def test_send_stale_after_turn_409_includes_latest(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "stale-409",
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
                "subject": "unread",
                "body": "blocking",
            },
        )
        resp = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "reply",
                "body": "too soon",
                "after_turn": 1,
            },
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "unread_turns_exist"
        assert detail["latest_turn_number"] == 2
        assert detail["provided_after_turn"] == 1
