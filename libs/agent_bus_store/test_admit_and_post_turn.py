"""Tests for atomic dispatch-admit-and-post (CDP supplied-thread path)."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.cdp_pointer_sweep import format_cdp_generate_pointer_body
from agent_bus_store.db import (
    admit_and_post_turn,
    create_thread,
    get_thread_with_links,
    init_db,
    insert_turn,
)


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def _active_thread_with_turns(*, turn_count: int = 4) -> str:
    row = create_thread(
        thread_id=None,
        slug="cdp-parent",
        lifecycle_state=None,
        tags=["type:generate"],
    )
    assert row is not None
    thread_id = row["id"]
    for n in range(1, turn_count + 1):
        insert_turn(
            thread=thread_id,
            from_agent="dispatch",
            to_agent="web-anthropic",
            subject=f"prior {n}",
            body=f"prior body {n}",
        )
    return thread_id


def test_admit_and_post_turn_link_and_pointer_same_txn(bus_db) -> None:
    thread_id = _active_thread_with_turns(turn_count=2)
    body = format_cdp_generate_pointer_body(
        model="cdp/opus-5.5",
        execution_id="exec-admit-post-1",
    )
    row = admit_and_post_turn(
        thread_id=thread_id,
        execution_id="exec-admit-post-1",
        pipeline_id="cdp-generate",
        caller_agent="dispatch",
        from_agent="dispatch",
        to_agent="web-anthropic",
        subject="cdp generate",
        body=body,
    )
    assert row["turn_count"] == 3
    detail = get_thread_with_links(thread_id)
    assert detail is not None
    assert len(detail["dispatch_links"]) == 1
    assert detail["dispatch_links"][0]["execution_id"] == "exec-admit-post-1"


def test_admit_and_post_turn_idempotent_same_execution(bus_db) -> None:
    thread_id = _active_thread_with_turns(turn_count=1)
    body = format_cdp_generate_pointer_body(
        model="cdp/opus-5.5",
        execution_id="exec-idem",
    )
    first = admit_and_post_turn(
        thread_id=thread_id,
        execution_id="exec-idem",
        pipeline_id="cdp-generate",
        caller_agent="dispatch",
        from_agent="dispatch",
        to_agent="web-anthropic",
        subject="cdp generate",
        body=body,
    )
    second = admit_and_post_turn(
        thread_id=thread_id,
        execution_id="exec-idem",
        pipeline_id="cdp-generate",
        caller_agent="dispatch",
        from_agent="dispatch",
        to_agent="web-anthropic",
        subject="cdp generate retry",
        body=body,
    )
    assert first["turn_count"] == second["turn_count"] == 2
    detail = get_thread_with_links(thread_id)
    assert detail is not None
    assert len(detail["dispatch_links"]) == 1


def test_dispatch_admit_and_post_route(bus_db) -> None:
    thread_id = _active_thread_with_turns(turn_count=0)
    payload = {
        "execution_id": "exec-route-admit-post",
        "pipeline_id": "cdp-generate",
        "caller_agent": "dispatch",
        "from_agent": "dispatch",
        "to_agent": "web-anthropic",
        "subject": "route",
        "body": format_cdp_generate_pointer_body(
            model="cdp/opus-5.5",
            execution_id="exec-route-admit-post",
        ),
    }
    resp = bus_db.post(f"/threads/{thread_id}/dispatch-admit-and-post", json=payload)
    assert resp.status_code == 200
    assert resp.json()["turn_count"] == 1


def test_admit_and_post_turn_sqlite_busy_no_partial_commit(
    bus_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_id = _active_thread_with_turns(turn_count=0)
    body = format_cdp_generate_pointer_body(
        model="cdp/opus-5.5",
        execution_id="exec-busy",
    )

    real_insert = __import__(
        "agent_bus_store.db.threads_atomic", fromlist=["_insert_dispatch_link_row"]
    )._insert_dispatch_link_row

    def _raise_after_turn(*args, **kwargs):
        real_insert(*args, **kwargs)
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        "agent_bus_store.db.threads_atomic._insert_dispatch_link_row",
        _raise_after_turn,
    )
    with pytest.raises(sqlite3.OperationalError):
        admit_and_post_turn(
            thread_id=thread_id,
            execution_id="exec-busy",
            pipeline_id="cdp-generate",
            caller_agent="dispatch",
            from_agent="dispatch",
            to_agent="web-anthropic",
            subject="cdp",
            body=body,
        )
    detail = get_thread_with_links(thread_id)
    assert detail is not None
    assert detail["turn_count"] == 0
    assert not detail["dispatch_links"]
