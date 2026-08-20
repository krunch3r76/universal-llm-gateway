"""Seated CSE without an in-flight project-ask must be visible to hop identity.

AC2 for todo:hop-cadence-join-half-heal: capture and refuse read the same
joined snap so a registry-seated operator with empty active-work rows is not
LOOKUP_FAILED / hop-allowed as if the chair were empty.
"""

from __future__ import annotations

import time

from unittest.mock import MagicMock, patch

from claude_bundles.hop_cadence_seat_snap import (
    SEATED_NO_STREAM_EXECUTION,
    attach_seated_rows,
    identity_rows,
    seated_rows_from_registry_records,
)
from services.git_integration_worker.cursor_auto.cdp_escalation import (
    read_cdp_lane_snapshot,
)
from claude_bundles.hop_seat_cutover import (
    refuse_cadence_hop_for_live_seat,
    running_registration_ids,
)
from services.git_integration_worker.cursor_auto.hop_cadence import (
    evaluate_capacity_gate,
)
from services.git_integration_worker.cursor_auto.hop_cadence_predecessor import (
    PredecessorConfirmError,
    PredecessorVerdict,
    capture_predecessor_at_hop,
)

_THREAD = "7186"
_REG = "reg-seated-idle"


def _seated_row(**overrides: object) -> dict:
    row = {
        "registration_id": _REG,
        "execution_id": SEATED_NO_STREAM_EXECUTION,
        "parent_thread": _THREAD,
        "purpose": "operator-proxy",
        "status": "running",
        "source": "cse-session-registry",
    }
    row.update(overrides)
    return row


def _idle_store_snap(*, seated: bool) -> dict:
    snap: dict = {
        "rows": [],
        "running_count": 0,
        "free_slots": 3,
        "at_soft_limit": False,
        "at_hard_limit": False,
    }
    if seated:
        snap["seated_rows"] = [_seated_row()]
    return snap


def test_capture_lookup_fails_when_only_execution_store_is_empty() -> None:
    """Today's defect: seated CSE with no project-ask is invisible on active-work rows."""
    row = {"thread_id": _THREAD, "registration_id": _REG}
    result = capture_predecessor_at_hop(row, _idle_store_snap(seated=False))
    assert isinstance(result, PredecessorConfirmError)
    assert result.reason == "predecessor_execution_lookup_failed"


def test_seated_cse_without_project_ask_visible_to_predecessor_lookup() -> None:
    """Fails on pre-join code: seated_rows are ignored; passes once identity_rows unions them."""
    row = {"thread_id": _THREAD, "registration_id": _REG}
    snap = _idle_store_snap(seated=True)
    handle = capture_predecessor_at_hop(row, snap)
    assert not isinstance(handle, PredecessorConfirmError)
    assert handle.verdict == PredecessorVerdict.INCUMBENT_RECORDED
    assert handle.registration_id == _REG
    assert handle.execution_id == SEATED_NO_STREAM_EXECUTION


def test_refuse_and_capture_agree_who_is_seated() -> None:
    snap = _idle_store_snap(seated=True)
    row = {"thread_id": _THREAD, "registration_id": _REG, "last_hop_at": time.time() - 60.0}
    handle = capture_predecessor_at_hop(row, snap)
    refuse, reason, evidence = refuse_cadence_hop_for_live_seat(row, snap)
    assert handle.verdict == PredecessorVerdict.INCUMBENT_RECORDED
    assert handle.registration_id == _REG
    # F6: idle seated identity is visible to capture, not to host-running refuse.
    assert refuse is False
    assert reason is None
    assert evidence == {}
    assert _REG not in running_registration_ids(snap)


def test_dormant_seat_does_not_refuse_successor() -> None:
    """Seat-open dormant projected as seated running must not refuse a successor hop."""
    snap = {
        "rows": [],
        "running_count": 0,
        "free_slots": 3,
        "at_soft_limit": False,
        "at_hard_limit": False,
        "seated_rows": [
            _seated_row(status="running"),
        ],
    }
    row = {"thread_id": _THREAD, "registration_id": _REG, "last_hop_at": time.time() - 60.0}
    handle = capture_predecessor_at_hop(row, snap)
    refuse, reason, _evidence = refuse_cadence_hop_for_live_seat(row, snap)
    assert handle.verdict == PredecessorVerdict.INCUMBENT_RECORDED
    assert refuse is False
    assert reason is None
    assert _REG not in running_registration_ids(snap)


def test_first_hop_still_allowed_against_seated_idle() -> None:
    snap = _idle_store_snap(seated=True)
    refuse, reason, _ = refuse_cadence_hop_for_live_seat(
        {"registration_id": _REG, "last_hop_at": None},
        snap,
    )
    assert refuse is False
    assert reason is None


def test_capacity_scalars_ignore_seated_rows() -> None:
    snap = _idle_store_snap(seated=True)
    snap["seated_rows"] = [_seated_row(registration_id=f"reg-{i}") for i in range(7)]
    gate = evaluate_capacity_gate(snap)
    assert gate.blocked is False
    assert gate.running_count == 0
    assert gate.free_slots == 3


def test_identity_rows_store_wins_on_registration_id() -> None:
    store = {
        "registration_id": _REG,
        "execution_id": "exec-live",
        "status": "running",
        "purpose": "operator-proxy",
        "parent_thread": _THREAD,
    }
    snap = {
        "rows": [store],
        "seated_rows": [_seated_row(execution_id=SEATED_NO_STREAM_EXECUTION)],
    }
    rows = identity_rows(snap)
    assert len(rows) == 1
    assert rows[0]["execution_id"] == "exec-live"


def test_seated_rows_from_registry_records_skips_non_listable() -> None:
    seated = seated_rows_from_registry_records(
        {
            "alive": {
                "registration_id": "reg-a",
                "status": "active",
                "purpose": "operator-proxy",
                "parent_thread": _THREAD,
            },
            "dead": {
                "registration_id": "reg-b",
                "status": "released",
                "purpose": "operator-proxy",
                "parent_thread": _THREAD,
            },
            "parked": {
                "registration_id": "reg-c",
                "status": "dormant",
                "purpose": "operator-proxy",
                "parent_thread": _THREAD,
            },
        }
    )
    assert [row["registration_id"] for row in seated] == ["reg-a"]
    assert seated[0]["execution_id"] == SEATED_NO_STREAM_EXECUTION
    assert seated[0]["status"] == "running"


def test_attach_seated_rows_does_not_rewrite_admission_rows() -> None:
    snap = {"rows": [{"registration_id": "exec-only"}], "running_count": 2}
    out = attach_seated_rows(snap, [_seated_row()])
    assert out["rows"] == [{"registration_id": "exec-only"}]
    assert out["running_count"] == 2
    assert out["seated_rows"][0]["registration_id"] == _REG


def test_read_cdp_lane_snapshot_attaches_registry_seats() -> None:
    client = MagicMock()
    client._request.return_value = {
        "rows": [],
        "running_count": 0,
        "free_slots": 3,
        "at_hard_limit": False,
    }
    with patch(
        "claude_bundles.hop_cadence_seat_snap.read_registry_seated_rows",
        return_value=[_seated_row()],
    ):
        snap = read_cdp_lane_snapshot(client=client)
    assert snap["rows"] == []
    assert snap["running_count"] == 0
    assert snap["seated_rows"][0]["registration_id"] == _REG
    assert "observed_at" in snap
