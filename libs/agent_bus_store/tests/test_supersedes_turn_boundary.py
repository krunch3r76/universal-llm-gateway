"""Supersedes_turn boundary translation tests."""

from __future__ import annotations

import pytest

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.supersedes_turn_boundary import (
    SupersedesTurnNotFoundError,
    resolve_supersedes_turn,
)
from fastapi.testclient import TestClient


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
