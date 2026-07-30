"""Unit tests for structural CHECKPOINT kind detector."""

from __future__ import annotations

from agent_bus_store.checkpoint_kind_detector import (
    checkpoint_auto_stamp_enabled,
    is_birth_shaped_checkpoint,
    is_bootstrap_structural_checkpoint,
    is_steady_state_structural_checkpoint,
    is_structural_checkpoint,
)


def test_birth_shaped_not_structural() -> None:
    assert is_birth_shaped_checkpoint(subject="CHECKPOINT wave 1", supersedes_turn=None)
    assert not is_structural_checkpoint(
        subject="CHECKPOINT wave 1",
        thread_tags=[],
        supersedes_turn=None,
    )


def test_bootstrap_structural_before_role_root() -> None:
    assert is_bootstrap_structural_checkpoint(
        subject="CHECKPOINT wave 2",
        thread_tags=["project:ulg"],
        supersedes_turn=1,
    )
    assert is_structural_checkpoint(
        subject="CHECKPOINT wave 2",
        thread_tags=["project:ulg"],
        supersedes_turn=1,
    )


def test_steady_state_structural_with_role_root() -> None:
    assert is_steady_state_structural_checkpoint(
        subject="CHECKPOINT wave 3",
        thread_tags=["role:root"],
        supersedes_turn=2,
    )


def test_non_checkpoint_subject_false() -> None:
    assert not is_structural_checkpoint(
        subject="WIP: shipping",
        thread_tags=["role:root"],
        supersedes_turn=1,
    )


def test_auto_stamp_default_off(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_BUS_CHECKPOINT_AUTO_STAMP", raising=False)
    assert not checkpoint_auto_stamp_enabled()


def test_auto_stamp_flag_on(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BUS_CHECKPOINT_AUTO_STAMP", "true")
    assert checkpoint_auto_stamp_enabled()


def test_add_tags_preserves_existing(tmp_path, monkeypatch) -> None:
    from agent_bus_store.db.threads import add_tags

    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    from agent_bus_store import create_app
    from agent_bus_store.auth import require_token
    from fastapi.testclient import TestClient

    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "tag-merge",
                "from": "cursor",
                "to": "web",
                "subject": "seed",
                "body": "hello",
                "tags": ["project:ulg"],
            },
        )
        thread_id = seed.json()["thread"]["id"]
    detail = add_tags(thread_id, ["type:bug"])
    assert detail is not None
    assert set(detail["tags"]) == {"project:ulg", "type:bug"}
    detail2 = add_tags(thread_id, ["agent:cursor"])
    assert set(detail2["tags"]) == {"project:ulg", "type:bug", "agent:cursor"}


def test_auto_stamp_bootstrap_when_flag_on(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BUS_CHECKPOINT_AUTO_STAMP", "true")
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    from agent_bus_store import create_app
    from agent_bus_store.auth import require_token
    from fastapi.testclient import TestClient

    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "stamp-bootstrap",
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
        tags = resp.json()["thread"]["tags"]
        assert "role:root" in tags

