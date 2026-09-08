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
from agent_bus_store.reconcile import (
    RECONCILE_RESUME_GRACE_S,
    _orphan_body_for_reason,
    reconcile_orphaned_dispatches,
)
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
        assert reconcile_orphaned_dispatches() == 0
        count = reconcile_orphaned_dispatches()

    assert count == 1
    assert len(emitted) == 1
    assert emitted[0]["execution_id"] == "exec-orphan"
    assert "reason" in emitted[0]

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
        assert reconcile_orphaned_dispatches() == 0
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


def test_instant_failed_typeerror_class_reconcile_backfill(bus_db) -> None:
    """Instant FAILED (TypeError class) with NULL link — reconcile backfills failed."""
    thread_row, *_ = create_thread_with_turn(
        slug="instant-fail",
        from_agent="dispatch",
        to_agent="claude-cursor",
        subject="cursor-sdk generate",
        body="pointer",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-instant",
        pipeline_id="cursor-sdk-generate",
    )
    insert_turn(
        thread=thread_id,
        from_agent="cursor-sdk",
        to_agent="dispatch",
        subject="cursor-sdk dispatch disp-type FAILED",
        body=(
            '{"code":"CURSOR_SDK_DISPATCH","message":'
            '"\'Timeout\' object cannot be interpreted as an integer"}'
        ),
    )
    detail_before = get_thread_with_links(thread_id)
    assert detail_before is not None
    assert detail_before["dispatch_links"][0]["terminal_status"] is None

    with patch("agent_bus_store.reconcile.emit_dispatch_orphaned") as mock_orphan:
        before = reconcile_orphaned_dispatches()
        after = reconcile_orphaned_dispatches()

    assert before == 1
    assert after == 0
    mock_orphan.assert_not_called()
    detail_after = get_thread_with_links(thread_id)
    assert detail_after is not None
    assert detail_after["dispatch_links"][0]["terminal_status"] == "failed"


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


def _admit_orphan_thread(
    bus_db, *, slug: str = "orphan-live", execution_id: str = "exec-live"
):
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
    thread_id, _ = _admit_orphan_thread(
        bus_db, slug="orphan-null", execution_id="exec-null"
    )

    def _dead(**_kwargs: object):
        return LivenessVerdict.ALLOW_ORPHAN, "probe_status_null", None

    with patch("agent_bus_store.reconcile.emit_dispatch_orphaned"):
        with patch(
            "agent_bus_store.reconcile.evaluate_link_liveness", side_effect=_dead
        ):
            assert reconcile_orphaned_dispatches() == 0
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
        with patch(
            "agent_bus_store.reconcile.evaluate_link_liveness", side_effect=_stale
        ):
            assert reconcile_orphaned_dispatches() == 0
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
        with patch(
            "agent_bus_store.reconcile.evaluate_link_liveness", side_effect=_mismatch
        ):
            assert reconcile_orphaned_dispatches() == 0
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
        with patch(
            "agent_bus_store.reconcile.evaluate_link_liveness", side_effect=_terminal
        ):
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


def test_grace_default_remains_zero() -> None:
    assert RECONCILE_RESUME_GRACE_S == 0


def test_allow_orphan_first_strike_stamps_without_reap(bus_db) -> None:
    thread_id, execution_id = _admit_orphan_thread(
        bus_db, slug="orphan-strike1", execution_id="exec-strike1"
    )

    def _null(**_kwargs: object):
        return LivenessVerdict.ALLOW_ORPHAN, "probe_status_null", None

    with patch("agent_bus_store.reconcile.emit_dispatch_orphaned") as mock_orphan:
        with patch(
            "agent_bus_store.reconcile.evaluate_link_liveness", side_effect=_null
        ):
            assert reconcile_orphaned_dispatches() == 0

    mock_orphan.assert_not_called()
    assert _orphan_turns(thread_id) == []
    with connect() as conn:
        row = conn.execute(
            "SELECT terminal_status, liveness_probe_deferred_reason "
            "FROM thread_dispatch_links WHERE thread_id=? AND execution_id=?",
            (thread_id, execution_id),
        ).fetchone()
    assert row["terminal_status"] is None
    assert row["liveness_probe_deferred_reason"] == "pending_orphan:probe_status_null"


def test_allow_orphan_second_strike_reaps(bus_db) -> None:
    thread_id, execution_id = _admit_orphan_thread(
        bus_db, slug="orphan-strike2", execution_id="exec-strike2"
    )

    def _null(**_kwargs: object):
        return LivenessVerdict.ALLOW_ORPHAN, "probe_status_null", None

    emitted: list[dict] = []
    with patch(
        "agent_bus_store.reconcile.emit_dispatch_orphaned",
        side_effect=lambda **kwargs: emitted.append(kwargs),
    ):
        with patch(
            "agent_bus_store.reconcile.evaluate_link_liveness", side_effect=_null
        ):
            assert reconcile_orphaned_dispatches() == 0
            assert reconcile_orphaned_dispatches() == 1

    assert len(_orphan_turns(thread_id)) == 1
    assert emitted[0]["reason"] == "probe_status_null"
    with connect() as conn:
        row = conn.execute(
            "SELECT terminal_status, liveness_probe_deferred_reason "
            "FROM thread_dispatch_links WHERE thread_id=? AND execution_id=?",
            (thread_id, execution_id),
        ).fetchone()
    assert row["terminal_status"] == "failed"
    assert row["liveness_probe_deferred_reason"] is None


def test_skip_live_resets_orphan_strike(bus_db) -> None:
    thread_id, execution_id = _admit_orphan_thread(
        bus_db, slug="orphan-reset", execution_id="exec-reset"
    )
    calls = {"n": 0}

    def _null_then_live_then_null(**_kwargs: object):
        calls["n"] += 1
        if calls["n"] == 2:
            return LivenessVerdict.SKIP_LIVE, "worker_live", None
        return LivenessVerdict.ALLOW_ORPHAN, "probe_status_null", None

    with patch("agent_bus_store.reconcile.emit_dispatch_orphaned"):
        with patch(
            "agent_bus_store.reconcile.evaluate_link_liveness",
            side_effect=_null_then_live_then_null,
        ):
            assert reconcile_orphaned_dispatches() == 0
            assert reconcile_orphaned_dispatches() == 0
            assert reconcile_orphaned_dispatches() == 0

    assert _orphan_turns(thread_id) == []
    with connect() as conn:
        row = conn.execute(
            "SELECT liveness_probe_deferred_reason "
            "FROM thread_dispatch_links WHERE thread_id=? AND execution_id=?",
            (thread_id, execution_id),
        ).fetchone()
    assert row["liveness_probe_deferred_reason"] == "pending_orphan:probe_status_null"


def test_orphan_body_differs_by_reason() -> None:
    not_found = _orphan_body_for_reason("probe_not_found", "exec-x")
    stale = _orphan_body_for_reason("heartbeat_stale", "exec-x")
    assert not_found != stale
    assert "likely service restart" not in not_found
    assert "likely process death or restart" in stale
    assert "exec-x" in not_found
    fallback = _orphan_body_for_reason("future_reason", "exec-x")
    assert "future_reason" in fallback
    assert "likely service restart" not in fallback


def test_cdp_generate_link_untouched_by_reconcile_sweep(bus_db) -> None:
    """M4: reconcile allowlist skips cdp-generate links (L1-AC-j)."""
    thread_row, *_ = create_thread_with_turn(
        slug="cdp-m4-solo",
        from_agent="dispatch",
        to_agent="web-anthropic",
        subject="cdp generate",
        body="pointer",
        lifecycle_state="active",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-cdp-only",
        pipeline_id="cdp-generate",
        caller_agent="cursor",
    )
    assert reconcile_orphaned_dispatches() == 0
    detail = get_thread_with_links(thread_id)
    assert detail is not None
    link = detail["dispatch_links"][0]
    assert link["pipeline_id"] == "cdp-generate"
    assert link["terminal_status"] is None


def test_cdp_generate_link_survives_with_sdk_closeout_present(bus_db) -> None:
    """M4: cdp-generate link survives when SDK closeout is on the thread (L1-AC-j)."""
    thread_row, *_ = create_thread_with_turn(
        slug="cdp-m4-mixed",
        from_agent="dispatch",
        to_agent="cursor-sdk",
        subject="cursor-sdk generate",
        body="pointer",
        lifecycle_state="active",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-sdk-m4",
        pipeline_id="cursor-sdk-generate",
        caller_agent="cursor",
    )
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-cdp-m4",
        pipeline_id="cdp-generate",
        caller_agent="cursor",
    )
    with TestClient(_app(bus_db)) as client:
        client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "cursor-sdk",
                "to": "dispatch",
                "subject": "cursor-sdk dispatch closeout",
                "body": "done",
                "after_turn": 1,
            },
        )
    reconcile_orphaned_dispatches()
    links = {
        row["execution_id"]: row
        for row in (get_thread_with_links(thread_id) or {})["dispatch_links"]
    }
    assert links["exec-cdp-m4"]["terminal_status"] is None


def test_cdp_generate_link_untouched_after_mismatch_probes(bus_db) -> None:
    """M4: execution_id_mismatch probe does not touch cdp-generate links (L1-AC-k)."""
    thread_row, *_ = create_thread_with_turn(
        slug="cdp-m4-mismatch",
        from_agent="dispatch",
        to_agent="web-anthropic",
        subject="cdp generate",
        body="pointer",
        lifecycle_state="active",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-cdp-mismatch",
        pipeline_id="cdp-generate",
        caller_agent="cursor",
    )

    def _mismatch(**_kwargs: object):
        return LivenessVerdict.ALLOW_ORPHAN, "execution_id_mismatch", None

    with patch(
        "agent_bus_store.reconcile.evaluate_link_liveness", side_effect=_mismatch
    ):
        assert reconcile_orphaned_dispatches() == 0
        assert reconcile_orphaned_dispatches() == 0

    link = get_thread_with_links(thread_id)["dispatch_links"][0]
    assert link["terminal_status"] is None


def test_dispatch_admit_on_active_completed_lifecycle(bus_db) -> None:
    """L1-AC-g: dispatch-admit succeeds when status=active and lifecycle=completed."""
    thread_row, *_ = create_thread_with_turn(
        slug="cdp-reopen-admit",
        from_agent="dispatch",
        to_agent="web-anthropic",
        subject="cdp pointer",
        body="prior pointer",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    with connect() as conn:
        conn.execute(
            "UPDATE threads SET status = 'active', bus_lifecycle_state = 'completed' "
            "WHERE id = ?",
            (thread_id,),
        )
        conn.commit()

    with TestClient(_app(bus_db)) as client:
        resp = client.post(
            f"/threads/{thread_id}/dispatch-admit",
            json={
                "execution_id": "exec-reopen-admit",
                "pipeline_id": "cdp-generate",
                "caller_agent": "cursor",
            },
        )
        assert resp.status_code in (200, 201), resp.text
        link_resp = client.get("/dispatch-links/exec-reopen-admit")
        assert link_resp.status_code == 200
        assert link_resp.json()["terminal_status"] is None
        detail = get_thread_with_links(thread_id)
        assert detail is not None
        assert detail["bus_lifecycle_state"] == "active"
