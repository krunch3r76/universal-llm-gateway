"""Integration: quiet-with-WIP sweep emits once, posts once, then no-ops."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from agent_bus_store.db import admit_dispatch, create_thread_with_turn, init_db
from agent_bus_store.db.connection import connect
from agent_bus_store.db.turns import get_turns
from agent_bus_store.quiet_sweep import sweep_quiet_with_wip


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    return db_path


def test_sweep_emits_and_posts_once(bus_db) -> None:
    """Temp DB: one event, one alarm row, one turn; second sweep is a no-op."""
    seat = "web-anthropic"
    thread_row, *_ = create_thread_with_turn(
        slug="quiet-wip",
        from_agent=seat,
        to_agent="cursor-auto",
        subject="commission work",
        body="please run the implement",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-quiet-1",
        pipeline_id="cursor-sdk-generate",
        caller_agent=seat,
    )
    # Age the seat turn past threshold (default probe uses threshold_s=60 here).
    old = (datetime.now(UTC) - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at = ? WHERE thread = ?",
            (old, thread_id),
        )
        conn.execute(
            "UPDATE threads SET bus_lifecycle_state = 'active', updated_at = ? "
            "WHERE id = ?",
            (old, thread_id),
        )

    emitted: list[dict] = []

    def _capture(**kwargs):
        emitted.append(kwargs)

    with (
        patch(
            "agent_bus_store.quiet_sweep.emit_quiet_with_wip_fired",
            side_effect=_capture,
        ),
        patch(
            "agent_bus_store.quiet_sweep._licensed_park",
            return_value=False,
        ),
    ):
        n1 = sweep_quiet_with_wip(threshold_s=60.0)
        n2 = sweep_quiet_with_wip(threshold_s=60.0)

    assert n1 == 1
    assert n2 == 0
    assert len(emitted) == 1
    assert emitted[0]["thread"] == thread_id
    assert emitted[0]["reason"] == "wip_in_flight"
    assert emitted[0]["wip_execution_ids"] == ["exec-quiet-1"]

    with connect() as conn:
        alarms = conn.execute(
            "SELECT alarm_id, status, reason FROM thread_quiet_alarms "
            "WHERE thread_id = ?",
            (thread_id,),
        ).fetchall()
    assert len(alarms) == 1
    assert alarms[0]["status"] == "open"
    assert alarms[0]["reason"] == "wip_in_flight"

    turns = get_turns(thread=thread_id, last=20)
    quiet_turns = [
        t
        for t in turns
        if (t.get("subject") or "") == "Quiet with work in flight"
        and t.get("from_agent") == "dispatch"
    ]
    assert len(quiet_turns) == 1
    assert quiet_turns[0]["to_agent"] == seat


def test_mission_lane_active_birth_in_sweep_candidacy(bus_db) -> None:
    """AC: newly created mission lane with active lifecycle is A′ candidate."""
    from agent_bus_store.quiet_sweep import _candidate_thread_ids

    thread_row, *_ = create_thread_with_turn(
        slug="mission-a-prime-cand",
        from_agent="web-anthropic",
        to_agent="cursor-auto",
        subject="TYPE: DIRECTIVE",
        body="mission lane birth",
        tags=["lane:cursor-auto", "bus_lifecycle:persistent"],
        lifecycle_state="active",
    )
    thread_id = thread_row["id"]
    assert thread_row["bus_lifecycle_state"] == "active"
    assert thread_id in _candidate_thread_ids()


def test_null_lifecycle_not_in_sweep_candidacy(bus_db) -> None:
    """NULL remains unenrolled — predicate must not widen to include it."""
    from agent_bus_store.quiet_sweep import _candidate_thread_ids

    thread_row, *_ = create_thread_with_turn(
        slug="unenrolled-null",
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="plain send",
        body="no lifecycle",
    )
    thread_id = thread_row["id"]
    assert thread_row["bus_lifecycle_state"] is None
    assert thread_id not in _candidate_thread_ids()


def test_migration_006_backfills_null_mission_lanes(bus_db) -> None:
    """Backfill enrolls lane:cursor-auto NULL rows; leaves other NULLs alone."""
    from agent_bus_store.db.migrations import migration_006
    from agent_bus_store.quiet_sweep import _candidate_thread_ids

    mission, *_ = create_thread_with_turn(
        slug="pre-enroll-mission",
        from_agent="web-anthropic",
        to_agent="cursor-auto",
        subject="old mission",
        body="unenrolled at birth",
        tags=["lane:cursor-auto", "bus_lifecycle:persistent"],
    )
    other, *_ = create_thread_with_turn(
        slug="pre-enroll-other",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="standing",
        body="not a mission lane",
        tags=["project:ulg"],
    )
    mission_id = mission["id"]
    other_id = other["id"]
    assert mission["bus_lifecycle_state"] is None
    assert other["bus_lifecycle_state"] is None

    with connect() as conn:
        migration_006.run(conn)

    with connect() as conn:
        m_state = conn.execute(
            "SELECT bus_lifecycle_state FROM threads WHERE id = ?",
            (mission_id,),
        ).fetchone()["bus_lifecycle_state"]
        o_state = conn.execute(
            "SELECT bus_lifecycle_state FROM threads WHERE id = ?",
            (other_id,),
        ).fetchone()["bus_lifecycle_state"]
    assert m_state == "active"
    assert o_state is None
    assert mission_id in _candidate_thread_ids()
    assert other_id not in _candidate_thread_ids()
