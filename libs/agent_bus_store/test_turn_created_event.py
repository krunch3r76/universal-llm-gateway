"""Falsifier tests for store-layer mcp.agentbus.turn.created emission."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import admit_dispatch, create_thread, init_db
from agent_bus_store.db.threads_atomic import claim_and_post_turn
from agent_bus_store.db.turns import insert_turn


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def capture_turn_created(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _capture(signal: str, payload: dict[str, Any], *, role: str = "observation") -> None:
        calls.append({"signal": signal, "payload": payload, "role": role})

    monkeypatch.setattr("agent_bus_store.events.turn_created._publish", _capture)
    return calls


def _assert_turn_created(
    call: dict[str, Any],
    *,
    thread: str,
    turn_number: int,
    from_agent: str,
    to_agent: str,
    subject: str,
    created_at: str | None = None,
) -> None:
    assert call["signal"] == "mcp.agentbus.turn.created"
    assert call["role"] == "coordination"
    payload = call["payload"]
    assert payload["thread"] == thread
    assert payload["turn_number"] == turn_number
    assert payload["from_agent"] == from_agent
    assert payload["to_agent"] == to_agent
    assert payload["subject"] == subject
    assert isinstance(payload["turn_id"], int)
    assert "body" not in payload
    if created_at is not None:
        assert payload["created_at"] == created_at


def test_insert_turn_emits_turn_created(bus_db, capture_turn_created) -> None:
    thread_id = create_thread(thread_id=None, slug="insert-turn-emit")["id"]
    turn_id, created_at, turn_number = insert_turn(
        thread=thread_id,
        from_agent="cursor",
        to_agent="web",
        subject="first via insert_turn",
        body="secret body must not appear in event",
    )
    assert len(capture_turn_created) == 1
    _assert_turn_created(
        capture_turn_created[0],
        thread=thread_id,
        turn_number=turn_number,
        from_agent="cursor",
        to_agent="web",
        subject="first via insert_turn",
        created_at=created_at,
    )
    assert capture_turn_created[0]["payload"]["turn_id"] == turn_id


def test_post_turns_route_emits_turn_created(bus_db, capture_turn_created) -> None:
    create = bus_db.post(
        "/threads/with-turn",
        json={
            "slug": "post-turns-route",
            "from": "cursor",
            "to": "web",
            "subject": "seed",
            "body": "seed body",
        },
    )
    assert create.status_code == 201, create.text
    thread_id = create.json()["thread"]["id"]
    capture_turn_created.clear()

    resp = bus_db.post(
        "/turns",
        json={
            "thread": thread_id,
            "from": "web",
            "to": "cursor",
            "subject": "second via POST /turns",
            "body": "reply body",
        },
    )
    assert resp.status_code == 201, resp.text
    assert len(capture_turn_created) == 1
    _assert_turn_created(
        capture_turn_created[0],
        thread=thread_id,
        turn_number=2,
        from_agent="web",
        to_agent="cursor",
        subject="second via POST /turns",
    )


def test_create_thread_with_turn_emits_turn_created(bus_db, capture_turn_created) -> None:
    capture_turn_created.clear()
    resp = bus_db.post(
        "/threads/with-turn",
        json={
            "slug": "with-turn-emit",
            "from": "dispatch",
            "to": "cursor",
            "subject": "atomic create",
            "body": "pointer",
        },
    )
    assert resp.status_code == 201, resp.text
    thread_id = resp.json()["thread"]["id"]
    assert len(capture_turn_created) == 1
    _assert_turn_created(
        capture_turn_created[0],
        thread=thread_id,
        turn_number=1,
        from_agent="dispatch",
        to_agent="cursor",
        subject="atomic create",
    )


def test_claim_and_post_turn_emits_turn_created(bus_db, capture_turn_created) -> None:
    thread_id = create_thread(
        thread_id=None,
        slug="claim-post-emit",
        lifecycle_state="pending",
        tags=["cursor-sdk-generate"],
    )["id"]
    capture_turn_created.clear()

    claim_and_post_turn(
        thread_id=thread_id,
        execution_id="exec-turn-created",
        pipeline_id="cursor-sdk-generate",
        caller_agent="dispatch",
        from_agent="dispatch",
        to_agent="cursor-sdk:dispatch:exec-turn-created",
        subject="claim pointer",
        body="pointer body",
    )
    assert len(capture_turn_created) == 1
    _assert_turn_created(
        capture_turn_created[0],
        thread=thread_id,
        turn_number=1,
        from_agent="dispatch",
        to_agent="cursor-sdk:dispatch:exec-turn-created",
        subject="claim pointer",
    )


def test_active_thread_subsequent_turn_emits_without_lifecycle_only(
    bus_db, capture_turn_created, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifecycle_calls: list[dict[str, Any]] = []

    def _capture_lifecycle(*args: Any, **kwargs: Any) -> None:
        lifecycle_calls.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(
        "agent_bus_store.db.lifecycle.emit_lifecycle_transitioned",
        _capture_lifecycle,
    )

    thread_id = create_thread(
        thread_id=None,
        slug="active-subsequent",
        lifecycle_state="pending",
    )["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-active-subsequent",
        pipeline_id="cursor-sdk-generate",
        caller_agent="dispatch",
    )
    capture_turn_created.clear()
    lifecycle_calls.clear()

    insert_turn(
        thread=thread_id,
        from_agent="dispatch",
        to_agent="web",
        subject="activating turn",
        body="activates admitted -> active",
    )
    assert len(capture_turn_created) == 1
    assert len(lifecycle_calls) == 1

    capture_turn_created.clear()
    lifecycle_calls.clear()

    insert_turn(
        thread=thread_id,
        from_agent="web",
        to_agent="dispatch",
        subject="checkpoint on active thread",
        body="no lifecycle edge expected",
    )
    assert len(capture_turn_created) == 1
    assert lifecycle_calls == []
    _assert_turn_created(
        capture_turn_created[0],
        thread=thread_id,
        turn_number=2,
        from_agent="web",
        to_agent="dispatch",
        subject="checkpoint on active thread",
    )
