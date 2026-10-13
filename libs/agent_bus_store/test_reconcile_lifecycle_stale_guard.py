"""Reconcile orphan lifecycle transitions must not clobber a fresher thread row."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from agent_bus_store.db import (
    admit_dispatch,
    create_thread_with_turn,
    get_thread_with_links,
    init_db,
)
from agent_bus_store.db.connection import connect
from agent_bus_store.db.lifecycle import _transition_lifecycle_state
from agent_bus_store.db.turns import insert_turn
from agent_bus_store.reconcile import reconcile_orphaned_dispatches
from agent_bus_store.sdk_liveness import LivenessVerdict

pytestmark = pytest.mark.offline


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    return db_path


def _seed_orphan_candidate(*, slug: str, execution_id: str) -> str:
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
    return thread_id


def test_orphan_lifecycle_skipped_when_updated_at_bumps(bus_db) -> None:
    """Concurrency: a turn after the lifecycle SELECT must block the reap transition."""
    thread_id = _seed_orphan_candidate(slug="stale-race", execution_id="exec-stale-race")

    def _null(**_kwargs: object):
        return LivenessVerdict.ALLOW_ORPHAN, "probe_status_null", None

    real = _transition_lifecycle_state

    def _race_guarded_transition(conn, tid, to_state, trigger, **kwargs):
        if kwargs.get("stale_before") is not None:
            insert_turn(
                thread=tid,
                from_agent="dispatch",
                to_agent="claude-cursor",
                subject="still working",
                body="turn between lifecycle select and update",
            )
            bumped = (datetime.now(UTC) + timedelta(seconds=2)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            conn.execute(
                "UPDATE threads SET updated_at = ? WHERE id = ?",
                (bumped, tid),
            )
        return real(conn, tid, to_state, trigger, **kwargs)

    with patch("agent_bus_store.reconcile.emit_dispatch_orphaned"):
        with patch(
            "agent_bus_store.reconcile.evaluate_link_liveness", side_effect=_null
        ):
            with patch(
                "agent_bus_store.reconcile._transition_lifecycle_state",
                side_effect=_race_guarded_transition,
            ):
                assert reconcile_orphaned_dispatches() == 0
                assert reconcile_orphaned_dispatches() == 1

    detail = get_thread_with_links(thread_id)
    assert detail is not None
    assert detail["dispatch_links"][0]["terminal_status"] == "failed"
    assert detail["bus_lifecycle_state"] == "active"
    assert detail["status"] != "closed"


def test_orphan_lifecycle_skipped_when_same_second_turn(bus_db) -> None:
    """Same-second updated_at: a turn after the SELECT must still block the transition."""
    thread_id = _seed_orphan_candidate(
        slug="stale-same-sec", execution_id="exec-stale-same-sec"
    )

    def _null(**_kwargs: object):
        return LivenessVerdict.ALLOW_ORPHAN, "probe_status_null", None

    real = _transition_lifecycle_state

    def _race_guarded_transition(conn, tid, to_state, trigger, **kwargs):
        stale_before = kwargs.get("stale_before")
        stale_max = kwargs.get("stale_max_turn_number")
        if stale_before is not None and stale_max is not None:
            insert_turn(
                thread=tid,
                from_agent="dispatch",
                to_agent="claude-cursor",
                subject="still working",
                body="turn between lifecycle select and update (same second)",
            )
            conn.execute(
                "UPDATE threads SET updated_at = ? WHERE id = ?",
                (stale_before, tid),
            )
        return real(conn, tid, to_state, trigger, **kwargs)

    with patch("agent_bus_store.reconcile.emit_dispatch_orphaned"):
        with patch(
            "agent_bus_store.reconcile.evaluate_link_liveness", side_effect=_null
        ):
            with patch(
                "agent_bus_store.reconcile._transition_lifecycle_state",
                side_effect=_race_guarded_transition,
            ):
                assert reconcile_orphaned_dispatches() == 0
                assert reconcile_orphaned_dispatches() == 1

    detail = get_thread_with_links(thread_id)
    assert detail is not None
    assert detail["dispatch_links"][0]["terminal_status"] == "failed"
    assert detail["bus_lifecycle_state"] == "active"
    assert detail["status"] != "closed"


def test_orphan_lifecycle_still_transitions_when_row_unchanged(bus_db) -> None:
    """Guard must not block a normal orphan reap when updated_at is unchanged."""
    thread_id = _seed_orphan_candidate(
        slug="stale-ok", execution_id="exec-stale-ok"
    )

    def _null(**_kwargs: object):
        return LivenessVerdict.ALLOW_ORPHAN, "probe_status_null", None

    with patch("agent_bus_store.reconcile.emit_dispatch_orphaned"):
        with patch(
            "agent_bus_store.reconcile.evaluate_link_liveness", side_effect=_null
        ):
            assert reconcile_orphaned_dispatches() == 0
            assert reconcile_orphaned_dispatches() == 1

    detail = get_thread_with_links(thread_id)
    assert detail is not None
    assert detail["bus_lifecycle_state"] == "abandoned"
    assert detail["status"] == "closed"
    with connect() as conn:
        turn_count = conn.execute(
            "SELECT COUNT(*) AS n FROM turns WHERE thread = ?",
            (thread_id,),
        ).fetchone()
    assert int(turn_count["n"]) >= 2
