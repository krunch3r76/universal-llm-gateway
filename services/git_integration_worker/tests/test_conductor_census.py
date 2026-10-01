"""Conductor mission census — one read of every mission's state and release call."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.conductor_census import (
    CENSUS_STATES,
    census,
    classify_mission_row,
    render_json,
    render_table,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    CursorDispatchLedger._instance = None
    yield tmp_path
    CursorDispatchLedger._instance = None


def _admit(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    thread_id: str,
    work_key: str,
    terminal_status: str | None = "completed",
    record_patch: dict | None = None,
) -> None:
    req = CursorDispatchRequest(
        thread_id=thread_id,
        model="cursor/grok-4.7",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message="conductor",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="conductor-hop",
        resolved_model="grok-4.7",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            model_id="grok-4.7",
        ),
        contract="conductor",
        source_repo=f"/repo-{thread_id}",
        lease_key=f"/repo-{thread_id}",
        work_key=work_key,
        source_ref=work_key,
    )
    patch = {"contract": "conductor", "lane": "B", "summoning_thread_id": "13707"}
    if record_patch:
        patch.update(record_patch)
    ledger.merge_record_json(dispatch_id=dispatch_id, patch=patch)
    if terminal_status:
        ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status=terminal_status)


def test_census_lists_every_mission_with_state_and_release() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        dispatch_id="dd8ced48",
        thread_id="13713",
        work_key="todo:cdp-display-seat-swap",
        record_patch={
            "closeout_stop_tokens": ["ROW_HOP"],
            "hop_parked": True,
            "hop_park_reason": "hop_budget_no_progress_cap",
            "hop_seq": 2,
        },
    )
    _admit(
        ledger,
        dispatch_id="fb19d651",
        thread_id="13676",
        work_key="todo:master-baseline-test-failures",
        record_patch={
            "closeout_stop_tokens": ["CONSULT_PENDING"],
            "closeout_turn": 12,
            "consult_summoning_after_turn": 40,
        },
    )
    _admit(
        ledger,
        dispatch_id="852217cc",
        thread_id="13618",
        work_key="todo:seed-conductor-own-worker-summon",
        record_patch={"closeout_stop_tokens": ["DONE"]},
    )
    _admit(
        ledger,
        dispatch_id="live-1",
        thread_id="13999",
        work_key="todo:still-running",
        terminal_status=None,
    )
    with ledger._connect() as conn:
        rows = census(conn, now=datetime.now(UTC) + timedelta(minutes=5))
    by_key = {r.work_key: r for r in rows}
    assert set(by_key) == {
        "todo:cdp-display-seat-swap",
        "todo:master-baseline-test-failures",
        "todo:seed-conductor-own-worker-summon",
        "todo:still-running",
    }

    parked = by_key["todo:cdp-display-seat-swap"]
    assert parked.state == "budget_parked"
    assert parked.stop == "ROW_HOP"
    assert "hop_budget_no_progress_cap" in parked.reason
    assert parked.release == _release(
        work_key="todo:cdp-display-seat-swap",
        thread_id="13713",
        dispatch_thread_id="13707",
        model="cursor/grok-4.7",
        extra='generation_options={"hop_park_release": true}',
    )
    assert parked.summoning_thread_id == "13707"
    assert parked.seconds_in_state is not None and parked.seconds_in_state > 250

    consult = by_key["todo:master-baseline-test-failures"]
    assert consult.state == "consult_pending"
    assert "thread 13676 after turn 12" in consult.reason
    assert "summoning 13707 after turn 40" in consult.reason
    assert "consult_pending_continue" in consult.release

    done = by_key["todo:seed-conductor-own-worker-summon"]
    assert done.state == "done"
    assert done.release == "none"

    live = by_key["todo:still-running"]
    assert live.state == "live"
    assert live.status == "admitted"

    for row in rows:
        assert row.state in CENSUS_STATES


def test_census_open_only_hides_done_and_orders_longest_stuck_first() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        dispatch_id="old-park",
        thread_id="1",
        work_key="todo:old",
        record_patch={"closeout_stop_tokens": ["ROW_HOP"], "hop_parked": True},
    )
    _admit(
        ledger,
        dispatch_id="done-1",
        thread_id="2",
        work_key="todo:finished",
        record_patch={"closeout_stop_tokens": ["DONE"]},
    )
    _admit(
        ledger,
        dispatch_id="new-park",
        thread_id="3",
        work_key="todo:new",
        record_patch={"closeout_stop_tokens": ["ROW_HOP"], "hop_parked": True},
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET terminal_at=? WHERE dispatch_id='old-park'",
            ((datetime.now(UTC) - timedelta(hours=6)).isoformat(),),
        )
    with ledger._connect() as conn:
        rows = census(conn, open_only=True)
    assert [r.work_key for r in rows] == ["todo:old", "todo:new"]


def test_census_latest_row_per_mission_wins() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        dispatch_id="hop-1",
        thread_id="7",
        work_key="todo:chain",
        record_patch={"closeout_stop_tokens": ["ROW_HOP"], "hop_successor": "hop-2"},
    )
    _admit(
        ledger,
        dispatch_id="hop-2",
        thread_id="7",
        work_key="todo:chain",
        record_patch={"closeout_stop_tokens": ["ROW_HOP"], "hop_seq": 1},
    )
    with ledger._connect() as conn:
        rows = census(conn)
    assert len(rows) == 1
    assert rows[0].dispatch_id == "hop-2"
    assert rows[0].state == "hop_owed"
    assert "hop reactor / watchdog" in rows[0].release


def test_restart_park_row_names_the_resume_call() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        dispatch_id="parked-1",
        thread_id="13691",
        work_key="todo:lane-b-git-integrity",
        terminal_status="cancelled",
        record_patch={"closeout_stop_tokens": ["PARKED_TRANSPORT"]},
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET park_kind='park_for_restart', "
            "park_intent_id='d4bbdc67' WHERE dispatch_id='parked-1'"
        )
    with ledger._connect() as conn:
        rows = census(conn)
    assert rows[0].state == "restart_parked"
    assert "d4bbdc67" in rows[0].reason
    assert rows[0].release == (
        "GIW resumes it after the restart drains; else "
        + _release(
            work_key="todo:lane-b-git-integrity",
            thread_id="13691",
            dispatch_thread_id="13707",
            model="cursor/grok-4.7",
            extra='resume_of="parked-1"',
        )
    )


@pytest.mark.parametrize(
    ("record", "status", "expected_state"),
    [
        ({"closeout_stop_tokens": ["HOLD_MERGE"]}, "completed", "hold_merge"),
        ({"closeout_stop_tokens": ["OPERATOR_GATE"]}, "completed", "operator_gate"),
        ({"closeout_stop_tokens": ["ROW_PINNED"]}, "completed", "pinned"),
        (
            {"closeout_stop_tokens": ["PARKED_TRANSPORT"], "closeout_harvest_owed": True},
            "completed",
            "transport_parked",
        ),
        (
            {
                "closeout_stop_tokens": ["ROW_HOP"],
                "hop_admit_error": {"last_error": "CURSOR_LANE_PIN_FAILED", "last_status_code": 503},
            },
            "completed",
            "hop_owed",
        ),
        ({}, "failed", "crashed"),
        ({}, "completed", "silent"),
    ],
)
def test_classify_designed_stops_and_crashes(
    record: dict, status: str, expected_state: str
) -> None:
    row = {
        "dispatch_id": "x-1",
        "thread_id": "42",
        "work_key": "todo:x",
        "status": status,
        "terminal_at": datetime.now(UTC).isoformat(),
        "record_json": json.dumps(record),
    }
    entry = classify_mission_row(row)
    assert entry.state == expected_state
    assert 'resume_of="x-1"' in entry.release
    if expected_state == "hop_owed" and record.get("hop_admit_error"):
        assert "503" in entry.reason
        assert "CURSOR_LANE_PIN_FAILED" in entry.reason


def _release(
    *,
    work_key: str,
    thread_id: str,
    dispatch_thread_id: str,
    extra: str = "",
    model: str = "grok-4.7",
    knobs: str = '{"effort":"high"}',
) -> str:
    tail = f", {extra}" if extra else ""
    return (
        'team_dispatch(op="generate", seat="cursor-sdk", contract="conductor", '
        f'source_ref="{work_key}", lane="B", reuse_thread="{thread_id}", '
        f'dispatch_thread_id="{dispatch_thread_id}", model="{model}", '
        f"model_knobs={knobs}{tail})"
    )


def test_release_uses_summoning_thread_model_and_knobs() -> None:
    row = classify_mission_row(
        {
            "dispatch_id": "park-1",
            "thread_id": "13713",
            "work_key": "todo:ordinary",
            "status": "completed",
            "resolved_model": "grok-4.7",
            "terminal_at": datetime.now(UTC).isoformat(),
            "record_json": json.dumps(
                {
                    "summoning_thread_id": "13707",
                    "model": "cursor/grok-4.7",
                    "model_knobs": {"effort": "xhigh", "fast": "true"},
                    "closeout_stop_tokens": ["ROW_HOP"],
                    "hop_parked": True,
                }
            ),
        }
    )
    assert row.release == _release(
        work_key="todo:ordinary",
        thread_id="13713",
        dispatch_thread_id="13707",
        model="cursor/grok-4.7",
        knobs='{"effort":"xhigh","fast":"true"}',
        extra='generation_options={"hop_park_release": true}',
    )


def test_release_on_operator_proxy_lane_uses_worker_thread() -> None:
    row = classify_mission_row(
        {
            "dispatch_id": "park-12286",
            "thread_id": "13724",
            "work_key": "todo:from-operator",
            "status": "completed",
            "terminal_at": datetime.now(UTC).isoformat(),
            "record_json": json.dumps(
                {
                    "summoning_thread_id": "12286",
                    "closeout_stop_tokens": ["ROW_HOP"],
                    "hop_parked": True,
                }
            ),
        }
    )
    assert row.release == _release(
        work_key="todo:from-operator",
        thread_id="13724",
        dispatch_thread_id="13724",
        extra='generation_options={"hop_park_release": true}',
    )


def test_nested_wait_release_is_none() -> None:
    row = classify_mission_row(
        {
            "dispatch_id": "nest-1",
            "thread_id": "50",
            "work_key": "todo:nest",
            "status": "parked_waiting",
            "record_json": "{}",
        }
    )
    assert row.state == "nested_wait"
    assert row.release == "none (the nest closing resumes the parent)"


def test_succeeded_requires_successor_row() -> None:
    present = classify_mission_row(
        {
            "dispatch_id": "pred",
            "thread_id": "7",
            "work_key": "todo:chain",
            "status": "completed",
            "terminal_at": datetime.now(UTC).isoformat(),
            "record_json": json.dumps(
                {"closeout_stop_tokens": ["ROW_HOP"], "hop_successor": "succ"}
            ),
        },
        known_dispatch_ids={"pred", "succ"},
    )
    assert present.state == "succeeded"
    assert present.release == "none (follow the successor row)"

    missing = classify_mission_row(
        {
            "dispatch_id": "pred",
            "thread_id": "7",
            "work_key": "todo:chain",
            "status": "completed",
            "terminal_at": datetime.now(UTC).isoformat(),
            "record_json": json.dumps(
                {"closeout_stop_tokens": ["ROW_HOP"], "hop_successor": "ghost"}
            ),
        },
        known_dispatch_ids={"pred"},
    )
    assert missing.state == "hop_owed"
    assert missing.release == (
        "hop reactor / watchdog admits the successor; else "
        + _release(
            work_key="todo:chain",
            thread_id="7",
            dispatch_thread_id="7",
            extra='resume_of="pred"',
        )
    )


def test_released_budget_park_is_not_budget_parked() -> None:
    row = classify_mission_row(
        {
            "dispatch_id": "rel-1",
            "thread_id": "9",
            "work_key": "todo:released",
            "status": "completed",
            "terminal_at": datetime.now(UTC).isoformat(),
            "record_json": json.dumps(
                {
                    "closeout_stop_tokens": ["ROW_HOP"],
                    "hop_parked": True,
                    "hop_park_released_at": "2026-10-01T00:00:00+00:00",
                }
            ),
        }
    )
    assert row.state == "hop_owed"
    assert row.release == (
        "hop reactor / watchdog admits the successor; else "
        + _release(
            work_key="todo:released",
            thread_id="9",
            dispatch_thread_id="9",
            extra='resume_of="rel-1"',
        )
    )


def test_resumed_restart_park_does_not_promise_giw_resume() -> None:
    row = classify_mission_row(
        {
            "dispatch_id": "parked-resumed",
            "thread_id": "11",
            "work_key": "todo:resumed",
            "status": "cancelled",
            "park_kind": "park_for_restart",
            "park_resumed_by": "child-1",
            "terminal_at": datetime.now(UTC).isoformat(),
            "record_json": "{}",
        }
    )
    assert row.state == "silent"
    assert row.release == (
        "watchdog re-admits (budgeted); else "
        + _release(
            work_key="todo:resumed",
            thread_id="11",
            dispatch_thread_id="11",
            extra='resume_of="parked-resumed"',
        )
    )


def test_cancel_discard_is_finished_with_no_release() -> None:
    row = classify_mission_row(
        {
            "dispatch_id": "disc-1",
            "thread_id": "12",
            "work_key": "todo:discarded",
            "status": "cancelled",
            "park_kind": "cancel_discard",
            "terminal_at": datetime.now(UTC).isoformat(),
            "record_json": "{}",
        }
    )
    assert row.state == "done"
    assert row.release == "none"


def test_restart_park_past_expiry_does_not_promise_auto_resume() -> None:
    expired = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    row = classify_mission_row(
        {
            "dispatch_id": "parked-exp",
            "thread_id": "13",
            "work_key": "todo:expired-park",
            "status": "cancelled",
            "park_kind": "park_for_restart",
            "park_intent_id": "intent-exp",
            "park_expires_at": expired,
            "terminal_at": datetime.now(UTC).isoformat(),
            "record_json": "{}",
        }
    )
    assert row.state == "restart_parked"
    assert "window closed" in row.reason
    assert row.release == _release(
        work_key="todo:expired-park",
        thread_id="13",
        dispatch_thread_id="13",
        extra='resume_of="parked-exp"',
    )


def test_hop_owed_permanent_admit_error_does_not_promise_retry() -> None:
    row = classify_mission_row(
        {
            "dispatch_id": "bad-400",
            "thread_id": "14",
            "work_key": "todo:permanent",
            "status": "completed",
            "terminal_at": datetime.now(UTC).isoformat(),
            "record_json": json.dumps(
                {
                    "closeout_stop_tokens": ["ROW_HOP"],
                    "hop_admit_error": {
                        "last_error": "bad request",
                        "last_status_code": 400,
                        "retryable": False,
                        "attempts": 1,
                    },
                }
            ),
        }
    )
    assert row.state == "hop_owed"
    assert row.release == (
        "admit refusal is not retryable; else "
        + _release(
            work_key="todo:permanent",
            thread_id="14",
            dispatch_thread_id="14",
            extra='resume_of="bad-400"',
        )
    )


def test_transport_park_armed_despite_stale_harvest_false() -> None:
    row = classify_mission_row(
        {
            "dispatch_id": "tp-1",
            "thread_id": "15",
            "work_key": "todo:transport",
            "status": "completed",
            "terminal_at": datetime.now(UTC).isoformat(),
            "record_json": json.dumps(
                {
                    "closeout_stop_tokens": ["PARKED_TRANSPORT"],
                    "closeout_harvest_owed": False,
                    "hop_park_harvest_fired_at": "2026-10-01T00:00:00+00:00",
                    "closeout_turn": 3,
                }
            ),
        }
    )
    assert row.state == "transport_parked"
    assert row.release == (
        "a web-anthropic reply on thread 15 after turn 3 (park_harvest_continue); else "
        + _release(
            work_key="todo:transport",
            thread_id="15",
            dispatch_thread_id="15",
            extra='resume_of="tp-1"',
        )
    )


def test_admitted_successor_with_null_stamps_is_latest(tmp_path) -> None:
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        dispatch_id="pred-null",
        thread_id="70",
        work_key="todo:null-stamps",
        record_patch={"closeout_stop_tokens": ["ROW_HOP"], "hop_successor": "succ-null"},
    )
    _admit(
        ledger,
        dispatch_id="succ-null",
        thread_id="70",
        work_key="todo:null-stamps",
        terminal_status=None,
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET terminal_at=NULL, started_at=NULL, "
            "queued_at=NULL WHERE dispatch_id='succ-null'"
        )
        rows = census(conn)
    assert len(rows) == 1
    assert rows[0].dispatch_id == "succ-null"
    assert rows[0].state == "live"


def test_renderers_carry_the_release_call() -> None:
    row = classify_mission_row(
        {
            "dispatch_id": "dd8ced48",
            "thread_id": "13713",
            "work_key": "todo:cdp-display-seat-swap",
            "status": "completed",
            "terminal_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
            "record_json": json.dumps(
                {
                    "closeout_stop_tokens": ["ROW_HOP"],
                    "hop_parked": True,
                    "hop_park_reason": "hop_budget_no_progress_cap",
                }
            ),
        }
    )
    table = render_table([row])
    assert "todo:cdp-display-seat-swap" in table
    assert "budget_parked" in table
    assert "hop_park_release" in table
    payload = json.loads(render_json([row]))
    assert payload[0]["state"] == "budget_parked"
    assert payload[0]["thread_id"] == "13713"
    assert render_table([]).endswith("(no conductor missions in the ledger)")


def test_days_filter_marks_old_finished_rows_stale() -> None:
    """--days N relabels finished rows older than N days; a fresh done stays done."""
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        dispatch_id="old-done",
        thread_id="1",
        work_key="todo:old-done",
        record_patch={"closeout_stop_tokens": ["DONE"]},
    )
    _admit(
        ledger,
        dispatch_id="fresh-done",
        thread_id="2",
        work_key="todo:fresh-done",
        record_patch={"closeout_stop_tokens": ["DONE"]},
    )
    old = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    fresh = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET terminal_at=? WHERE dispatch_id='old-done'",
            (old,),
        )
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET terminal_at=? WHERE dispatch_id='fresh-done'",
            (fresh,),
        )
        rows = census(conn, days=7)
    by_id = {row.dispatch_id: row for row in rows}
    assert by_id["old-done"].state == "stale"
    assert by_id["fresh-done"].state == "done"
    assert "stale" in CENSUS_STATES


def test_operator_lane_comes_from_sessions_registry() -> None:
    """A sessions-registry operator lane is not summoned as dispatch_thread_id."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_census import (
        operator_lanes_from_sessions,
    )

    sessions = {
        "reg:op": {
            "purpose": "operator-proxy",
            "ids": {"lane_thread": "77777"},
        },
        "reg:review": {
            "purpose": "review",
            "ids": {"lane_thread": "88888"},
        },
    }
    lanes = operator_lanes_from_sessions(sessions)
    assert "77777" in lanes
    assert "88888" not in lanes
    row = classify_mission_row(
        {
            "dispatch_id": "hop-op",
            "thread_id": "14000",
            "work_key": "todo:op-lane",
            "status": "completed",
            "terminal_at": datetime.now(UTC).isoformat(),
            "record_json": json.dumps(
                {
                    "closeout_stop_tokens": ["ROW_HOP"],
                    "summoning_thread_id": "77777",
                }
            ),
        },
        operator_lanes=lanes,
    )
    assert 'dispatch_thread_id="14000"' in row.release
    assert 'dispatch_thread_id="77777"' not in row.release


def test_stacked_parks_on_one_lane_are_counted() -> None:
    """Two unreleased parks on the same thread are not collapsed to one."""
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        dispatch_id="park-a",
        thread_id="55",
        work_key="todo:stacked",
        terminal_status="cancelled",
        record_patch={"closeout_stop_tokens": ["ROW_HOP"]},
    )
    _admit(
        ledger,
        dispatch_id="park-b",
        thread_id="55",
        work_key="todo:stacked",
        terminal_status="cancelled",
        record_patch={"closeout_stop_tokens": ["ROW_HOP"]},
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET park_kind='park_for_restart' "
            "WHERE dispatch_id IN ('park-a', 'park-b')"
        )
        rows = census(conn)
    assert len(rows) == 1
    assert rows[0].stacked_parks == 2
    assert "stacked parks on lane: 2" in rows[0].reason


def test_partial_last_row_is_not_a_census_row() -> None:
    """A torn record_json is not classified, and the reader does not write."""
    import sqlite3

    from services.git_integration_worker.cursor_dispatch_ledger import (
        resolve_cursor_sdk_dispatch_ledger_path,
    )
    from services.git_integration_worker.cursor_sdk_closeout.conductor_census import (
        open_census_connection,
    )

    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger,
        dispatch_id="good-row",
        thread_id="3",
        work_key="todo:good",
        record_patch={"closeout_stop_tokens": ["DONE"]},
    )
    _admit(
        ledger,
        dispatch_id="torn-row",
        thread_id="4",
        work_key="todo:torn",
        record_patch={"closeout_stop_tokens": ["DONE"]},
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id='torn-row'",
            ('{"closeout_stop_tokens":',),
        )
    path = resolve_cursor_sdk_dispatch_ledger_path()
    reader = open_census_connection(path)
    try:
        rows = census(reader)
        assert [row.dispatch_id for row in rows] == ["good-row"]
        writer = sqlite3.connect(path, timeout=0.3)
        writer.execute("BEGIN IMMEDIATE")
        writer.rollback()
        writer.close()
        with pytest.raises(sqlite3.OperationalError):
            reader.execute("CREATE TABLE census_must_not_write(x)")
    finally:
        reader.close()
