"""L2 — maestro ↔ web-anthropic share one read pointer on a thread."""

from __future__ import annotations

from agent_bus_store.auth import require_token
from agent_bus_store.recipients import _inbox_recipient_slugs
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
                "from": "grok-bot-gc1",
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


def test_inbox_recipient_slugs_maestro_web_anthropic_symmetric() -> None:
    maestro = set(_inbox_recipient_slugs("maestro"))
    web = set(_inbox_recipient_slugs("web-anthropic"))
    assert maestro == web
    for legacy in ("claude-web", "web", "cdp", "web_anthropic"):
        assert legacy in maestro


def test_maestro_send_mark_read_spares_web_anthropic_memo_notes(tmp_path) -> None:
    """Hop mark_read must not stamp read_at on alias-pair MEMO/NOTE (a:38363)."""
    with _client(tmp_path) as client:
        thread_id = client.post(
            "/threads/with-turn",
            json={
                "slug": "l2-memo-spared",
                "from": "cursor",
                "to": "cursor",
                "subject": "REQUEST|seed",
                "body": "seed",
            },
        ).json()["thread"]["id"]
        client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "maestro",
                "to": "web-anthropic",
                "subject": "MEMO|maestro to web",
                "body": "m1",
            },
        )
        client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "web-anthropic",
                "to": "web-anthropic",
                "subject": "MEMO|web self",
                "body": "m2",
            },
        )
        client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web-anthropic",
                "subject": "FACT|should mark",
                "body": "plain unread",
            },
        )
        send = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "maestro",
                "to": "cursor",
                "subject": "CHECKPOINT|hop",
                "body": "hop",
                "after_turn": 0,
                "mark_read": True,
            },
        )
        assert send.status_code == 201, send.text
        memo_maestro = client.get(
            f"/turns/by-number?thread={thread_id}&turn_number=2"
        ).json()
        memo_web = client.get(
            f"/turns/by-number?thread={thread_id}&turn_number=3"
        ).json()
        fact = client.get(
            f"/turns/by-number?thread={thread_id}&turn_number=4"
        ).json()
        assert memo_maestro["read_at"] is None
        assert memo_web["read_at"] is None
        assert fact["read_at"] is not None


def test_maestro_mark_read_does_not_stamp_to_all(tmp_path) -> None:
    with _client(tmp_path) as client:
        thread_id = client.post(
            "/threads/with-turn",
            json={
                "slug": "l2-all-unmarked",
                "from": "cursor",
                "to": "all",
                "subject": "VERDICT|broadcast",
                "body": "b",
            },
        ).json()["thread"]["id"]
        client.patch(
            f"/threads/{thread_id}/turns/read-state",
            json={"through_turn": 1, "agent": "maestro"},
        )
        turn = client.get(f"/turns/by-number?thread={thread_id}&turn_number=1").json()
        assert turn["read_at"] is None
