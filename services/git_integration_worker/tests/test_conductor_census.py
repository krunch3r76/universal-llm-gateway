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
    assert '"hop_park_release": true' in parked.release
    assert 'reuse_thread="13713"' in parked.release
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
    assert 'resume_of="parked-1"' in rows[0].release


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
