"""Tests for atomic pending-shell claim-and-post (F3 Option A′)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db
from agent_bus_store.db.threads_atomic import (
    PendingShellContention,
    claim_and_post_turn,
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


def _pending_shell() -> str:
    row = create_thread(
        thread_id=None,
        slug="pending-shell",
        lifecycle_state="pending",
        tags=["cursor-sdk-generate"],
    )
    assert row is not None
    return row["id"]


def test_claim_and_post_turn_happy_path(bus_db) -> None:
    thread_id = _pending_shell()
    row = claim_and_post_turn(
        thread_id=thread_id,
        execution_id="exec-claim-1",
        pipeline_id="cursor-sdk-generate",
        caller_agent="dispatch",
        from_agent="dispatch",
        to_agent="cursor-sdk:dispatch:exec-claim-1",
        subject="implement packet",
        body="pointer body",
    )
    assert row["bus_lifecycle_state"] == "active"
    assert row["turn_count"] == 1
    assert row["dispatch_links"]
    assert row["dispatch_links"][0]["execution_id"] == "exec-claim-1"


def test_claim_and_post_turn_contention_after_first_claim(bus_db) -> None:
    thread_id = _pending_shell()
    claim_and_post_turn(
        thread_id=thread_id,
        execution_id="exec-claim-1",
        pipeline_id="cursor-sdk-generate",
        caller_agent="dispatch",
        from_agent="dispatch",
        to_agent="cursor-sdk:dispatch:exec-claim-1",
        subject="first",
        body="first pointer",
    )
    with pytest.raises(PendingShellContention):
        claim_and_post_turn(
            thread_id=thread_id,
            execution_id="exec-claim-2",
            pipeline_id="cursor-sdk-generate",
            caller_agent="dispatch",
            from_agent="dispatch",
            to_agent="cursor-sdk:dispatch:exec-claim-2",
            subject="second",
            body="second pointer",
        )


def test_dispatch_claim_and_post_route_409_on_contention(bus_db) -> None:
    thread_id = _pending_shell()
    payload = {
        "execution_id": "exec-route-1",
        "pipeline_id": "cursor-sdk-generate",
        "caller_agent": "dispatch",
        "from_agent": "dispatch",
        "to_agent": "cursor-sdk:dispatch:exec-route-1",
        "subject": "route test",
        "body": "pointer",
    }
    first = bus_db.post(f"/threads/{thread_id}/dispatch-claim-and-post", json=payload)
    assert first.status_code == 200
    assert first.json()["turn_count"] == 1

    second = bus_db.post(f"/threads/{thread_id}/dispatch-claim-and-post", json=payload)
    assert second.status_code == 409
    assert second.json()["detail"]["error"] == "pending_shell_contention"
