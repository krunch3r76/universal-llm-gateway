"""Supersedes_turn boundary translation tests."""

from __future__ import annotations

import pytest
from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.supersedes_turn_boundary import (
    SupersedesTurnNotFoundError,
    derive_supersedes_turn_for_send,
    find_latest_checkpoint_turn_number,
    resolve_supersedes_turn,
)
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app


def test_resolve_supersedes_turn_by_turn_number(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "sup-seed",
                "from": "cursor",
                "to": "web",
                "subject": "seed",
                "body": "hello",
            },
        )
        thread_id = seed.json()["thread"]["id"]
        resolved = resolve_supersedes_turn(thread=thread_id, turn_number=1)
        assert resolved is not None
        assert resolved.turn_number == 1
        assert resolved.turn_id > 0


def test_resolve_supersedes_turn_missing_422(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "sup-miss",
                "from": "cursor",
                "to": "web",
                "subject": "seed",
                "body": "hello",
            },
        )
        thread_id = seed.json()["thread"]["id"]
        with pytest.raises(SupersedesTurnNotFoundError):
            resolve_supersedes_turn(thread=thread_id, turn_number=99)


def test_send_echoes_supersede_fields(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "sup-echo",
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v1",
                "body": "cp1",
            },
        )
        thread_id = seed.json()["thread"]["id"]
        resp = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v2",
                "body": "cp2",
                "supersedes_turn": 1,
            },
        )
        assert resp.status_code == 201, resp.text
        turn = resp.json()["turn"]
        assert turn["superseded_turn_number"] == 1
        assert turn["superseded_turn_id"] > 0


def test_supersedes_turn_id_alias_accepted(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "sup-alias",
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v1",
                "body": "cp1",
            },
        )
        thread_id = seed.json()["thread"]["id"]
        turn_id = seed.json()["turn"]["id"]
        resp = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v2",
                "body": "cp2",
                "supersedes_turn_id": turn_id,
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["turn"]["superseded_turn_number"] == 1


def test_auto_derive_supersedes_latest_checkpoint_on_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BUS_CHECKPOINT_AUTO_SUPERSEDE", "1")
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "sup-auto-root",
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v1",
                "body": "cp1",
                "tags": ["role:root"],
            },
        )
        thread_id = seed.json()["thread"]["id"]
        cp2 = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v2",
                "body": "cp2",
                "supersedes_turn": 1,
            },
        )
        assert cp2.status_code == 201, cp2.text
        resp = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v3",
                "body": "cp3",
            },
        )
        assert resp.status_code == 201, resp.text
        turn = resp.json()["turn"]
        assert turn["superseded_turn_number"] == 2
        assert turn["superseded_turn_id"] > 0


def test_explicit_supersedes_turn_wins_over_auto_derive(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "sup-explicit",
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v1",
                "body": "cp1",
                "tags": ["role:root"],
            },
        )
        thread_id = seed.json()["thread"]["id"]
        client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v2",
                "body": "cp2",
                "supersedes_turn": 1,
            },
        )
        resp = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v3",
                "body": "cp3",
                "supersedes_turn": 1,
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["turn"]["superseded_turn_number"] == 1


def test_wrong_supersedes_turn_422(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "sup-wrong",
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v1",
                "body": "cp1",
                "tags": ["role:root"],
            },
        )
        thread_id = seed.json()["thread"]["id"]
        resp = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v2",
                "body": "cp2",
                "supersedes_turn": 99,
            },
        )
        assert resp.status_code == 422, resp.text


def test_non_checkpoint_continue_omits_auto_default(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "sup-non-cp",
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v1",
                "body": "cp1",
                "tags": ["role:root"],
            },
        )
        thread_id = seed.json()["thread"]["id"]
        resp = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "WIP: shipping",
                "body": "status",
            },
        )
        assert resp.status_code == 201, resp.text
        turn = resp.json()["turn"]
        assert turn.get("superseded_turn_number") is None
        assert turn.get("superseded_turn_id") is None


def test_birth_checkpoint_keeps_null_supersedes(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "sup-birth",
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v1",
                "body": "cp1",
                "tags": ["role:root"],
            },
        )
        assert seed.status_code == 201, seed.text
        turn = seed.json()["turn"]
        assert turn.get("superseded_turn_number") is None
        assert turn.get("superseded_turn_id") is None


def test_find_latest_checkpoint_turn_number(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "sup-find",
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v1",
                "body": "cp1",
            },
        )
        thread_id = seed.json()["thread"]["id"]
        client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "WIP",
                "body": "noise",
            },
        )
        client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v2",
                "body": "cp2",
                "supersedes_turn": 1,
            },
        )
        assert find_latest_checkpoint_turn_number(thread=thread_id) == 3


def test_derive_supersedes_turn_for_send_requires_role_root() -> None:
    assert (
        derive_supersedes_turn_for_send(
            thread="1",
            subject="CHECKPOINT wave 2",
            thread_tags=["project:ulg"],
            turn_number=None,
            turn_id_alias=None,
        )
        is None
    )


def test_reply_auto_derives_supersedes_like_send(tmp_path, monkeypatch) -> None:
    """POST /turns (reply op) must share send-path auto-derive when flag is on."""
    monkeypatch.setenv("AGENT_BUS_CHECKPOINT_AUTO_SUPERSEDE", "1")
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "sup-reply-auto",
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v1",
                "body": "cp1",
                "tags": ["role:root"],
            },
        )
        thread_id = seed.json()["thread"]["id"]
        client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v2",
                "body": "cp2",
                "supersedes_turn": 1,
            },
        )
        resp = client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v3",
                "body": "cp3",
            },
        )
        assert resp.status_code == 201, resp.text
        turn = resp.json()
        assert turn["superseded_turn_number"] == 2
        assert turn["superseded_turn_id"] > 0


def test_auto_derive_supersedes_disabled_by_default(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "sup-gate-off",
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v1",
                "body": "cp1",
                "tags": ["role:root"],
            },
        )
        thread_id = seed.json()["thread"]["id"]
        client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v2",
                "body": "cp2",
                "supersedes_turn": 1,
            },
        )
        resp = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT v3",
                "body": "cp3",
            },
        )
        assert resp.status_code == 201, resp.text
        turn = resp.json()["turn"]
        assert turn.get("superseded_turn_number") is None
        assert turn.get("superseded_turn_id") is None
