"""R10 predecessor handle — record and persist superseded ids on succession confirm."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto.hop_cadence_predecessor import (
    PRIOR_NONE_EXECUTION,
    PRIOR_NONE_REGISTRATION,
    PredecessorHandle,
    PredecessorVerdict,
    capture_predecessor_at_hop,
    predecessor_from_watch,
)
from services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile import (
    reconcile_succession_confirmations,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    advance_registration_on_confirm,
    load_watches,
    mark_hop_fired,
)

pytestmark = pytest.mark.offline

_NOW = 1_700_000_100.0
_EXEC = "exec-stargate-r10"
_INCUMBENT_EXEC = "exec-incumbent-r10"


def _snap(
    *,
    registration_id: str,
    execution_id: str = "exec-successor",
    admission_count: int = 2,
) -> dict:
    return {
        "admission_count": admission_count,
        "rows": [
            {
                "execution_id": execution_id,
                "registration_id": registration_id,
                "status": "running",
                "purpose": "operator-proxy",
            }
        ],
    }


def _incumbent_snap(*, reg: str = "reg-old", exec_id: str = _INCUMBENT_EXEC) -> dict:
    return {
        "admission_count": 2,
        "rows": [
            {
                "execution_id": exec_id,
                "registration_id": reg,
                "status": "running",
                "purpose": "operator-proxy",
            }
        ],
    }


def test_capture_incumbent_present_and_recorded() -> None:
    row = {"thread_id": "7119", "registration_id": "reg-old"}
    handle = capture_predecessor_at_hop(row, _incumbent_snap())
    assert handle.verdict == PredecessorVerdict.INCUMBENT_RECORDED
    assert handle.registration_id == "reg-old"
    assert handle.execution_id == _INCUMBENT_EXEC


def test_capture_genuinely_no_incumbent() -> None:
    row = {"thread_id": "7119"}
    handle = capture_predecessor_at_hop(row, _incumbent_snap())
    assert handle.verdict == PredecessorVerdict.FIRST_SEAT_ON_LANE
    assert handle.registration_id == PRIOR_NONE_REGISTRATION
    assert handle.execution_id == PRIOR_NONE_EXECUTION
    assert handle.absence_reason == "no_registration_id_on_watch_at_hop_fire"


def test_capture_lookup_fails_when_incumbent_missing_from_snapshot() -> None:
    row = {"thread_id": "7119", "registration_id": "reg-old"}
    empty_snap = {"rows": []}
    result = capture_predecessor_at_hop(row, empty_snap)
    from services.git_integration_worker.cursor_auto.hop_cadence_predecessor import (
        PredecessorConfirmError,
    )

    assert isinstance(result, PredecessorConfirmError)
    assert result.reason == "predecessor_execution_lookup_failed"


def test_mark_hop_fired_persists_predecessor_handle(tmp_path: Path) -> None:
    watch_path = tmp_path / "watches.json"
    snap = _incumbent_snap()
    from services.git_integration_worker.cursor_auto.hop_cadence_watch import save_watches

    save_watches(
        {"7119": {"thread_id": "7119", "registration_id": "reg-old", "seated_at": _NOW - 100.0}},
        watch_path,
    )
    mark_hop_fired(
        "7119",
        now=_NOW,
        path=watch_path,
        execution_id=_EXEC,
        active_work_snap=snap,
    )
    row = load_watches(watch_path)["7119"]
    assert row["superseded_registration_id"] == "reg-old"
    assert row["superseded_execution_id"] == _INCUMBENT_EXEC
    assert row["predecessor_verdict"] == PredecessorVerdict.INCUMBENT_RECORDED.value


def test_mark_hop_fired_refuses_when_lookup_fails(tmp_path: Path) -> None:
    watch_path = tmp_path / "watches.json"
    from services.git_integration_worker.cursor_auto.hop_cadence_watch import save_watches

    save_watches(
        {"7119": {"thread_id": "7119", "registration_id": "reg-old", "seated_at": _NOW - 100.0}},
        watch_path,
    )
    ok = mark_hop_fired(
        "7119",
        now=_NOW,
        path=watch_path,
        execution_id=_EXEC,
        active_work_snap={"rows": []},
    )
    assert ok is False
    row = load_watches(watch_path)["7119"]
    assert row.get("succession_status") != "pending"


def test_confirm_persists_both_ids_and_emits_events() -> None:
    row = {
        "thread_id": "6885",
        "registration_id": "reg-old",
        "successor_execution_id": "stargate-uuid",
        "pending_satellite_execution_id": "satellite-live",
        "superseded_registration_id": "reg-old",
        "superseded_execution_id": _INCUMBENT_EXEC,
        "predecessor_verdict": PredecessorVerdict.INCUMBENT_RECORDED.value,
    }
    watches = {"6885": dict(row)}
    snap = _snap(registration_id="reg-new", execution_id="satellite-live")

    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.load_watches",
        side_effect=lambda path=None: watches,
    ), patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.save_watches",
        side_effect=lambda data, path=None: watches.update(data) or None,
    ), patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.emit_succession_confirmed",
    ) as confirmed_mock, patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.emit_registration_advanced",
    ) as advanced_mock:
        result = reconcile_succession_confirmations(snapshot_reader=lambda: snap)

    assert len(result["confirmations"]) == 1
    assert result["errors"] == []
    confirmed_kwargs = confirmed_mock.call_args.kwargs
    assert confirmed_kwargs["prior_registration_id"] == "reg-old"
    assert confirmed_kwargs["superseded_execution_id"] == _INCUMBENT_EXEC
    advanced_kwargs = advanced_mock.call_args.kwargs
    assert advanced_kwargs["prior_registration_id"] == "reg-old"
    assert advanced_kwargs["superseded_execution_id"] == _INCUMBENT_EXEC
    assert advanced_kwargs["superseding_execution_id"] == "satellite-live"
    record = watches["6885"]["succession_confirm_record"]
    assert record["prior_registration_id"] == "reg-old"
    assert record["superseded_execution_id"] == _INCUMBENT_EXEC


def test_confirm_fails_loud_when_incumbent_reg_without_exec_id() -> None:
    row = {
        "thread_id": "6885",
        "registration_id": "reg-old",
        "successor_execution_id": "stargate-uuid",
        "pending_satellite_execution_id": "satellite-live",
        "superseded_registration_id": "reg-old",
        "predecessor_verdict": PredecessorVerdict.INCUMBENT_RECORDED.value,
    }
    watches = {"6885": dict(row)}
    snap = _snap(registration_id="reg-new", execution_id="satellite-live")

    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.load_watches",
        side_effect=lambda path=None: watches,
    ), patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.emit_succession_confirmed",
    ) as confirmed_mock:
        result = reconcile_succession_confirmations(snapshot_reader=lambda: snap)

    assert result["confirmations"] == []
    assert len(result["errors"]) == 1
    assert result["errors"][0]["reason"] == "incumbent_handle_incomplete"
    confirmed_mock.assert_not_called()


def test_confirm_first_seat_uses_explicit_sentinel() -> None:
    row = {
        "thread_id": "6885",
        "registration_id": "",
        "successor_execution_id": "stargate-uuid",
        "pending_satellite_execution_id": "satellite-live",
        "superseded_registration_id": PRIOR_NONE_REGISTRATION,
        "superseded_execution_id": PRIOR_NONE_EXECUTION,
        "predecessor_verdict": PredecessorVerdict.FIRST_SEAT_ON_LANE.value,
        "predecessor_absence_reason": "no_registration_id_on_watch_at_hop_fire",
    }
    handle = predecessor_from_watch(row)
    assert handle.verdict == PredecessorVerdict.FIRST_SEAT_ON_LANE
    assert handle.registration_id == PRIOR_NONE_REGISTRATION


def test_admission_count_decrements_when_superseded_terminalized() -> None:
    snap = {
        "admission_count": 3,
        "rows": [
            {
                "execution_id": "satellite-live",
                "registration_id": "reg-new",
                "status": "running",
            },
            {
                "execution_id": _INCUMBENT_EXEC,
                "registration_id": "reg-old",
                "status": "running",
            },
        ],
    }
    watches = {
        "6885": {
            "thread_id": "6885",
            "registration_id": "reg-old",
            "successor_execution_id": "stargate-uuid",
            "pending_satellite_execution_id": "satellite-live",
            "superseded_registration_id": "reg-old",
            "superseded_execution_id": _INCUMBENT_EXEC,
            "predecessor_verdict": PredecessorVerdict.INCUMBENT_RECORDED.value,
        }
    }

    def _release(handle: PredecessorHandle) -> dict[str, Any]:
        snap["admission_count"] -= 1
        snap["rows"] = [r for r in snap["rows"] if r["execution_id"] != handle.execution_id]
        return {"action": "terminalized", "execution_id": handle.execution_id}

    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.load_watches",
        side_effect=lambda path=None: watches,
    ), patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.save_watches",
        side_effect=lambda data, path=None: watches.update(data) or None,
    ), patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.emit_succession_confirmed",
    ), patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.emit_registration_advanced",
    ):
        before = snap["admission_count"]
        reconcile_succession_confirmations(snapshot_reader=lambda: snap, release_fn=_release)
        after = snap["admission_count"]

    assert before == 3
    assert after == 2


def test_advance_registration_on_confirm_uses_prior_from_handle() -> None:
    row = {
        "registration_id": "",
        "superseded_registration_id": "reg-old",
        "superseded_execution_id": _INCUMBENT_EXEC,
    }
    aw_row = {"registration_id": "reg-new", "execution_id": "exec-1", "status": "running"}
    updated, transition = advance_registration_on_confirm(
        row,
        matched_key="exec-1",
        active_work_row=aw_row,
        now=_NOW,
        prior_registration_id="reg-old",
    )
    assert transition == ("reg-old", "reg-new")
    assert updated["registration_id"] == "reg-new"
    assert updated["succession_confirm_record"]["prior_registration_id"] == "reg-old"
