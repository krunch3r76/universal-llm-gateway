"""Phase 1 orphan elimination — admission, terminate, reconcile (AC1–AC6)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

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
from agent_bus_store.sdk_liveness import LivenessVerdict


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
        # dispatch_links lives on the dedicated lineage read (G2), not on the
        # plain ThreadDetail response (G3 dropped the always-inconsistent field).
        lineage = client.get(f"/threads/{thread_id}/lineage")
    assert lineage.status_code == 200
    link = lineage.json()["dispatch_links"][0]
    assert link["terminal_status"] == "completed"


def _admit_orphan_thread(bus_db, *, slug: str = "orphan-live", execution_id: str = "exec-live"):
    thread_row, *_ = create_thread_with_turn(
        slug=slug,
        from_agent="dispatch",
        to_agent="claude-cursor",
        subject="cursor-sdk generate",
        body="pointer only",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id=execution_id,
        pipeline_id="cursor-sdk-generate",
        caller_agent="claude-web",
    )
    return thread_id, execution_id


def _orphan_turns(thread_id: str) -> list[dict]:
    turns = get_turns(thread=thread_id)
    return [
        t
        for t in turns
        if t["from_agent"] == "dispatch"
        and "orphaned" in (t.get("subject") or "").lower()
    ]


def test_live_running_probe_skips_orphan(bus_db) -> None:
    thread_id, execution_id = _admit_orphan_thread(bus_db)

    def _live(**_kwargs: object):
        return LivenessVerdict.SKIP_LIVE, "worker_live", None

    with patch("agent_bus_store.reconcile.evaluate_link_liveness", side_effect=_live):
        count = reconcile_orphaned_dispatches()

    assert count == 0
    assert _orphan_turns(thread_id) == []
    detail = get_thread_with_links(thread_id)
    assert detail is not None
    assert detail["dispatch_links"][0]["terminal_status"] is None


def test_probe_status_none_still_orphans(bus_db) -> None:
    thread_id, _ = _admit_orphan_thread(bus_db, slug="orphan-null", execution_id="exec-null")

    def _dead(**_kwargs: object):
        return LivenessVerdict.ALLOW_ORPHAN, "probe_status_null", None

    with patch("agent_bus_store.reconcile.emit_dispatch_orphaned"):
        with patch("agent_bus_store.reconcile.evaluate_link_liveness", side_effect=_dead):
            count = reconcile_orphaned_dispatches()

    assert count == 1
    assert len(_orphan_turns(thread_id)) == 1


def test_probe_timeout_defers_without_orphan(bus_db) -> None:
    thread_id, execution_id = _admit_orphan_thread(
        bus_db, slug="orphan-defer", execution_id="exec-defer"
    )

    def _defer(**_kwargs: object):
        return LivenessVerdict.DEFER, "probe_unreachable:timeout", None

    with patch("agent_bus_store.reconcile.evaluate_link_liveness", side_effect=_defer):
        count = reconcile_orphaned_dispatches()

    assert count == 0
    assert _orphan_turns(thread_id) == []
    with connect() as conn:
        row = conn.execute(
            "SELECT liveness_probe_deferred_at, liveness_probe_deferred_reason "
            "FROM thread_dispatch_links WHERE thread_id=? AND execution_id=?",
            (thread_id, execution_id),
        ).fetchone()
    assert row["liveness_probe_deferred_at"] is not None
    assert "probe_unreachable" in row["liveness_probe_deferred_reason"]


def test_stale_heartbeat_allows_orphan(bus_db) -> None:
    thread_id, _ = _admit_orphan_thread(
        bus_db, slug="orphan-stale", execution_id="exec-stale"
    )

    def _stale(**_kwargs: object):
        return LivenessVerdict.ALLOW_ORPHAN, "heartbeat_stale", None

    with patch("agent_bus_store.reconcile.emit_dispatch_orphaned"):
        with patch("agent_bus_store.reconcile.evaluate_link_liveness", side_effect=_stale):
            count = reconcile_orphaned_dispatches()

    assert count == 1
    assert len(_orphan_turns(thread_id)) == 1


def test_execution_id_mismatch_allows_orphan(bus_db) -> None:
    thread_id, _ = _admit_orphan_thread(
        bus_db, slug="orphan-mismatch", execution_id="exec-mismatch"
    )

    def _mismatch(**_kwargs: object):
        return LivenessVerdict.ALLOW_ORPHAN, "execution_id_mismatch", None

    with patch("agent_bus_store.reconcile.emit_dispatch_orphaned"):
        with patch("agent_bus_store.reconcile.evaluate_link_liveness", side_effect=_mismatch):
            count = reconcile_orphaned_dispatches()

    assert count == 1
    assert len(_orphan_turns(thread_id)) == 1


def test_probe_terminal_backfills_without_orphan_turn(bus_db) -> None:
    thread_id, execution_id = _admit_orphan_thread(
        bus_db, slug="orphan-terminal", execution_id="exec-terminal"
    )

    def _terminal(**_kwargs: object):
        return LivenessVerdict.TERMINAL_BACKFILL, "probe_terminal", "completed"

    with patch("agent_bus_store.reconcile.emit_dispatch_orphaned") as mock_orphan:
        with patch("agent_bus_store.reconcile.evaluate_link_liveness", side_effect=_terminal):
            count = reconcile_orphaned_dispatches()

    assert count == 1
    mock_orphan.assert_not_called()
    assert _orphan_turns(thread_id) == []
    detail = get_thread_with_links(thread_id)
    assert detail is not None
    assert detail["dispatch_links"][0]["terminal_status"] == "completed"


def test_deferred_retry_later_orphans(bus_db) -> None:
    thread_id, execution_id = _admit_orphan_thread(
        bus_db, slug="orphan-retry", execution_id="exec-retry"
    )
    calls = {"n": 0}

    def _defer_then_stale(**_kwargs: object):
        calls["n"] += 1
        if calls["n"] == 1:
            return LivenessVerdict.DEFER, "probe_unreachable:timeout", None
        return LivenessVerdict.ALLOW_ORPHAN, "heartbeat_stale", None

    with patch("agent_bus_store.reconcile.emit_dispatch_orphaned"):
        with patch(
            "agent_bus_store.reconcile.evaluate_link_liveness",
            side_effect=_defer_then_stale,
        ):
            assert reconcile_orphaned_dispatches() == 0
            assert reconcile_orphaned_dispatches() == 1

    assert len(_orphan_turns(thread_id)) == 1
    with connect() as conn:
        row = conn.execute(
            "SELECT liveness_probe_deferred_at FROM thread_dispatch_links "
            "WHERE thread_id=? AND execution_id=?",
            (thread_id, execution_id),
        ).fetchone()
    assert row["liveness_probe_deferred_at"] is None
