"""LOOKUP_FAILED observe — per-row first-reject + non-throwing emit (arc 7186)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from services.git_integration_worker.cursor_auto.cdp_escalation import (
    read_cdp_lane_snapshot,
)
from services.git_integration_worker.cursor_auto.hop_cadence_lookup_failed_observe import (
    LOOKUP_FAILED_ROW_CAP,
    REJECT_EXECUTION_ID,
    REJECT_PARENT_THREAD,
    REJECT_PURPOSE,
    REJECT_STATUS,
    SNAP_KIND_EMPTY,
    SNAP_KIND_FAIL_OPEN,
    SNAP_KIND_ROWS_PRESENT,
    classify_lookup_failed_snap,
    first_incumbent_reject,
)
from services.git_integration_worker.cursor_auto.hop_cadence_predecessor import (
    PredecessorConfirmError,
    capture_predecessor_at_hop,
    incumbents_on_lane,
)

pytestmark = pytest.mark.offline

_THREAD = "7246"
_REG = "reg-dead"
_READ_AT = "2026-08-15T14:29:25.302000+00:00"


def _lookup_failed_row() -> dict:
    return {"thread_id": _THREAD, "registration_id": _REG}


def _filtered_snap() -> dict:
    return {
        "observed_at": _READ_AT,
        "free_slots": 1,
        "running_count": 4,
        "rows": [
            {
                "parent_thread": "9999",
                "purpose": "operator-proxy",
                "status": "running",
                "execution_id": "e-other-lane",
            },
            {
                "parent_thread": _THREAD,
                "purpose": "ask",
                "status": "running",
                "execution_id": "e-wrong-purpose",
            },
            {
                "parent_thread": _THREAD,
                "purpose": "operator-proxy",
                "status": "done",
                "execution_id": "e-done",
            },
            {
                "parent_thread": _THREAD,
                "purpose": "operator-proxy",
                "status": "running",
                "execution_id": "",
            },
        ],
    }


def _capture_event(row: dict, snap: dict):
    with patch(
        "services.git_integration_worker.cursor_auto."
        "hop_cadence_lookup_failed_observe.emit_frontier_event"
    ) as emit:
        result = capture_predecessor_at_hop(row, snap)
    return result, emit


def test_lookup_failed_emits_per_row_rejection_detail() -> None:
    result, emit = _capture_event(_lookup_failed_row(), _filtered_snap())
    assert isinstance(result, PredecessorConfirmError)
    assert result.reason == "predecessor_execution_lookup_failed"
    emit.assert_called_once()
    event = emit.call_args.args[0]
    assert event.signal == "giw.cursor_auto.hop_cadence_lookup_failed_observe"
    payload = event.payload
    assert "signal" not in payload
    assert payload["observed_at"] == _READ_AT
    assert payload["snap_kind"] == SNAP_KIND_ROWS_PRESENT
    assert payload["snap_empty"] is False
    assert payload["fail_open"] is False
    assert payload["total_rows"] == 4
    assert payload["running_count"] == 4
    assert payload["free_slots"] == 1
    assert payload["watch_reg_hit"] is False
    assert payload["registration_id"] == _REG
    rejects = [row["first_reject"] for row in payload["row_details"]]
    assert rejects == [
        REJECT_PARENT_THREAD,
        REJECT_PURPOSE,
        REJECT_STATUS,
        REJECT_EXECUTION_ID,
    ]
    assert incumbents_on_lane(_filtered_snap(), _THREAD) == []


def test_emit_raise_still_returns_lookup_failed() -> None:
    row = _lookup_failed_row()
    snap = {"rows": [], "observed_at": _READ_AT}
    with patch(
        "services.git_integration_worker.cursor_auto."
        "hop_cadence_lookup_failed_observe.emit_frontier_event",
        side_effect=TypeError(
            "record() got multiple values for argument 'signal'"
        ),
    ):
        result = capture_predecessor_at_hop(row, snap)
    assert isinstance(result, PredecessorConfirmError)
    assert result.reason == "predecessor_execution_lookup_failed"
    assert result.thread_id == _THREAD
    assert result.detail == {
        "registration_id": _REG,
        "verdict": "lookup_failed",
    }


def test_empty_snap_distinct_from_rows_present_all_filtered() -> None:
    empty_result, empty_emit = _capture_event(
        _lookup_failed_row(),
        {"rows": [], "observed_at": _READ_AT, "free_slots": 3, "running_count": 0},
    )
    filtered_result, filtered_emit = _capture_event(
        _lookup_failed_row(),
        _filtered_snap(),
    )
    assert isinstance(empty_result, PredecessorConfirmError)
    assert isinstance(filtered_result, PredecessorConfirmError)
    assert empty_result.reason == filtered_result.reason == "predecessor_execution_lookup_failed"
    empty_payload = empty_emit.call_args.args[0].payload
    filtered_payload = filtered_emit.call_args.args[0].payload
    assert empty_payload["snap_kind"] == SNAP_KIND_EMPTY
    assert empty_payload["snap_empty"] is True
    assert empty_payload["total_rows"] == 0
    assert empty_payload["row_details"] == []
    assert filtered_payload["snap_kind"] == SNAP_KIND_ROWS_PRESENT
    assert filtered_payload["snap_empty"] is False
    assert filtered_payload["total_rows"] == 4
    assert len(filtered_payload["row_details"]) == 4
    assert empty_payload["snap_kind"] != filtered_payload["snap_kind"]


def test_fail_open_distinct_from_empty_store() -> None:
    fail_payload = classify_lookup_failed_snap(
        {"fail_open": True},
        thread_id=_THREAD,
        registration_id=_REG,
        watch_reg_hit=False,
    )
    empty_payload = classify_lookup_failed_snap(
        {"rows": []},
        thread_id=_THREAD,
        registration_id=_REG,
        watch_reg_hit=False,
    )
    assert fail_payload["snap_kind"] == SNAP_KIND_FAIL_OPEN
    assert fail_payload["fail_open"] is True
    assert empty_payload["snap_kind"] == SNAP_KIND_EMPTY
    assert empty_payload["fail_open"] is False
    assert fail_payload["snap_kind"] != empty_payload["snap_kind"]


def test_row_detail_cap_recorded() -> None:
    rows = [
        {
            "parent_thread": "x",
            "purpose": "ask",
            "status": "done",
            "execution_id": f"e{i}",
        }
        for i in range(LOOKUP_FAILED_ROW_CAP + 5)
    ]
    payload = classify_lookup_failed_snap(
        {"rows": rows, "observed_at": _READ_AT},
        thread_id=_THREAD,
        registration_id=_REG,
        watch_reg_hit=False,
    )
    assert payload["row_detail_cap"] == LOOKUP_FAILED_ROW_CAP
    assert len(payload["row_details"]) == LOOKUP_FAILED_ROW_CAP
    assert payload["row_detail_omitted"] == 5
    assert payload["total_rows"] == LOOKUP_FAILED_ROW_CAP + 5


def test_read_cdp_lane_snapshot_stamps_observed_at() -> None:
    client = MagicMock()
    client._request.return_value = {
        "rows": [],
        "free_slots": 2,
        "running_count": 1,
    }
    before = datetime.now(timezone.utc).isoformat()
    snap = read_cdp_lane_snapshot(client=client)
    after = datetime.now(timezone.utc).isoformat()
    assert before <= snap["observed_at"] <= after
    assert snap["rows"] == []
    assert snap["free_slots"] == 2


def test_read_cdp_lane_snapshot_preserves_server_observed_at() -> None:
    client = MagicMock()
    client._request.return_value = {
        "rows": [],
        "observed_at": _READ_AT,
    }
    snap = read_cdp_lane_snapshot(client=client)
    assert snap["observed_at"] == _READ_AT


def test_first_reject_order_matches_incumbents_on_lane() -> None:
    lane = _THREAD
    status_row = {
        "parent_thread": lane,
        "purpose": "operator-proxy",
        "status": "done",
        "execution_id": "e1",
    }
    purpose_row = {
        "parent_thread": lane,
        "purpose": "ask",
        "status": "running",
        "execution_id": "e2",
    }
    parent_row = {
        "parent_thread": "other",
        "purpose": "operator-proxy",
        "status": "running",
        "execution_id": "e3",
    }
    exec_row = {
        "parent_thread": lane,
        "purpose": "operator-proxy",
        "status": "running",
        "execution_id": "",
    }
    assert first_incumbent_reject(status_row, lane) == REJECT_STATUS
    assert first_incumbent_reject(purpose_row, lane) == REJECT_PURPOSE
    assert first_incumbent_reject(parent_row, lane) == REJECT_PARENT_THREAD
    assert first_incumbent_reject(exec_row, lane) == REJECT_EXECUTION_ID
    snap = {"rows": [status_row, purpose_row, parent_row, exec_row]}
    assert incumbents_on_lane(snap, lane) == []


def test_incumbent_hit_does_not_emit_observe() -> None:
    row = {"thread_id": _THREAD, "registration_id": "reg-live"}
    snap = {
        "observed_at": _READ_AT,
        "rows": [
            {
                "registration_id": "reg-live",
                "parent_thread": _THREAD,
                "purpose": "operator-proxy",
                "status": "running",
                "execution_id": "exec-live",
            }
        ],
    }
    with patch(
        "services.git_integration_worker.cursor_auto."
        "hop_cadence_lookup_failed_observe.emit_frontier_event"
    ) as emit:
        handle = capture_predecessor_at_hop(row, snap)
    emit.assert_not_called()
    assert not isinstance(handle, PredecessorConfirmError)
    assert handle.execution_id == "exec-live"
