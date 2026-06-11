"""Phase 1 orphan elimination — admission, terminate, reconcile (AC1–AC6)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import (
    admit_dispatch,
    create_thread_with_turn,
    get_thread_with_links,
    init_db,
    terminate_dispatch,
)
from agent_bus_store.db.connection import connect
from agent_bus_store.db.turns import get_turns, insert_turn
from agent_bus_store.reconcile import reconcile_orphaned_dispatches
from fastapi.testclient import TestClient


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    return db_path


def _app(bus_db):
    app = create_app(db_path=str(bus_db))
    app.dependency_overrides[require_token] = lambda: None
    return app


def test_ac1_pending_thread_admit_creates_link(bus_db) -> None:
    """AC1: pending→admitted with dispatch link row."""
    thread_row, *_ = create_thread_with_turn(
        slug="sdk-gen",
        from_agent="dispatch",
        to_agent="claude-cursor",
        subject="cursor-sdk generate",
        body="pointer",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    assert thread_row["bus_lifecycle_state"] == "pending"

    admitted = admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-ac1",
        pipeline_id="cursor-sdk-generate",
        caller_agent="claude-web",
    )
    assert admitted is not None
    assert admitted["bus_lifecycle_state"] == "admitted"
    links = admitted["dispatch_links"]
    assert len(links) == 1
    assert links[0]["execution_id"] == "exec-ac1"
    assert links[0]["pipeline_id"] == "cursor-sdk-generate"
    assert links[0]["terminal_status"] is None


def test_ac2_success_terminate_writes_terminal(bus_db) -> None:
    """AC2: terminate_dispatch sets completed + timestamps."""
    thread_row, *_ = create_thread_with_turn(
        slug="term-ok",
        from_agent="dispatch",
        to_agent="cursor",
        subject="handoff",
        body="brief",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-ac2",
        pipeline_id="cursor-sdk-generate",
    )
    row = terminate_dispatch(
        thread_id=thread_id, terminal_status="completed", execution_id="exec-ac2"
    )
    assert row is not None
    link = row["dispatch_links"][0]
    assert link["terminal_status"] == "completed"
    assert link["delivery_at"] is not None

    with connect() as conn:
        raw = conn.execute(
            "SELECT terminal_at FROM thread_dispatch_links WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
    assert raw["terminal_at"] is not None


def test_ac3_failure_terminate_writes_failed(bus_db) -> None:
    """AC3: failed terminal_status."""
    thread_row, *_ = create_thread_with_turn(
        slug="term-fail",
        from_agent="dispatch",
        to_agent="cursor",
        subject="handoff",
        body="brief",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-ac3",
        pipeline_id="cursor-sdk-generate",
    )
    row = terminate_dispatch(thread_id=thread_id, terminal_status="failed")
    assert row is not None
    assert row["dispatch_links"][0]["terminal_status"] == "failed"


def test_ac4_orphan_reconciled_loud(bus_db) -> None:
    """AC4: true orphan gets terminal turn + failed link + abandoned lifecycle."""
    thread_row, *_ = create_thread_with_turn(
        slug="orphan-1607",
        from_agent="dispatch",
        to_agent="claude-cursor",
        subject="cursor-sdk generate",
        body="pointer only",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-orphan",
        pipeline_id="cursor-sdk-generate",
        caller_agent="claude-web",
    )

    emitted: list[dict] = []

    with patch(
        "agent_bus_store.reconcile.emit_dispatch_orphaned",
        side_effect=lambda **kwargs: emitted.append(kwargs),
    ):
        count = reconcile_orphaned_dispatches()

    assert count == 1
    assert len(emitted) == 1
    assert emitted[0]["execution_id"] == "exec-orphan"

    turns = get_turns(thread=thread_id)
    orphan_turns = [
        t
        for t in turns
        if t["from_agent"] == "dispatch"
        and "orphaned" in (t.get("subject") or "").lower()
    ]
    assert len(orphan_turns) == 1
    assert "exec-orphan" in orphan_turns[0]["body"]

    detail = get_thread_with_links(thread_id)
    assert detail is not None
    assert detail["dispatch_links"][0]["terminal_status"] == "failed"
    assert detail["bus_lifecycle_state"] == "abandoned"


def test_ac5_reconcile_idempotent(bus_db) -> None:
    """AC5: second reconcile pass posts no duplicate orphan turn."""
    thread_row, *_ = create_thread_with_turn(
        slug="orphan-idem",
        from_agent="dispatch",
        to_agent="claude-cursor",
        subject="sdk",
        body="pointer",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-idem",
        pipeline_id="cursor-sdk-generate",
    )

    with patch("agent_bus_store.reconcile.emit_dispatch_orphaned"):
        reconcile_orphaned_dispatches()
        reconcile_orphaned_dispatches()

    turns = get_turns(thread=thread_id)
    orphan_turns = [t for t in turns if "orphaned" in (t.get("subject") or "").lower()]
    assert len(orphan_turns) == 1


def test_ac6_partial_dedup_backfills_terminal(bus_db) -> None:
    """AC6: cursor-sdk terminal turn present ⇒ backfill, no orphan turn."""
    thread_row, *_ = create_thread_with_turn(
        slug="partial",
        from_agent="dispatch",
        to_agent="claude-cursor",
        subject="sdk",
        body="pointer",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-partial",
        pipeline_id="cursor-sdk-generate",
    )
    insert_turn(
        thread=thread_id,
        from_agent="cursor-sdk",
        to_agent="dispatch",
        subject="cursor-sdk dispatch disp-x FAILED",
        body="error",
    )

    with patch("agent_bus_store.reconcile.emit_dispatch_orphaned") as mock_orphan:
        count = reconcile_orphaned_dispatches()

    assert count == 1
    mock_orphan.assert_not_called()
    detail = get_thread_with_links(thread_id)
    assert detail is not None
    assert detail["dispatch_links"][0]["terminal_status"] == "failed"
    turns = get_turns(thread=thread_id)
    assert not any("orphaned" in (t.get("subject") or "").lower() for t in turns)


def test_with_turn_lifecycle_passthrough(bus_db) -> None:
    """WI-1 route: lifecycle_state forwarded to create_thread_with_turn."""
    with TestClient(_app(bus_db)) as client:
        resp = client.post(
            "/threads/with-turn",
            json={
                "slug": "lifecycle-pass",
                "from": "dispatch",
                "to": "claude-cursor",
                "subject": "sdk",
                "body": "brief",
                "lifecycle_state": "pending",
            },
        )
    assert resp.status_code == 201
    assert resp.json()["thread"]["bus_lifecycle_state"] == "pending"


def test_dispatch_terminate_route(bus_db) -> None:
    with TestClient(_app(bus_db)) as client:
        created = client.post(
            "/threads/with-turn",
            json={
                "slug": "terminate-route",
                "from": "dispatch",
                "to": "cursor",
                "subject": "s",
                "body": "b",
                "lifecycle_state": "pending",
            },
        )
        thread_id = created.json()["thread"]["id"]
        client.post(
            f"/threads/{thread_id}/dispatch-admit",
            json={
                "execution_id": "e1",
                "pipeline_id": "cursor-sdk-generate",
            },
        )
        term = client.post(
            f"/threads/{thread_id}/dispatch-terminate",
            json={"terminal_status": "completed", "execution_id": "e1"},
        )
    assert term.status_code == 200
    link = term.json()["dispatch_links"][0]
    assert link["terminal_status"] == "completed"
