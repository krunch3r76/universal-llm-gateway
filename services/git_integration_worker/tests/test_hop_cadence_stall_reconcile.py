"""Tests for hop-cadence stall-revoke reconciler (arc 6928 Route A)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from claude_bundles.hop_cadence_id_map import proof_observes_harvest

from services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile import (
    REVOKE_BREAKER_N,
    STALL_OBSERVE_FLOOR_S,
    apply_event_to_watch,
    breaker_blocks_hop,
    confirm_succession_claim,
    reconcile_stall_revocations,
    record_succession_claim,
    revoke_succession_claim,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    evaluate_watch,
    load_watches,
    mark_hop_fired,
    save_watches,
)

pytestmark = pytest.mark.offline

_NOW = 1_700_000_000.0
_EXEC = "exec-stargate-01"
_SAT = "sat-jupiter-01"


def _pending_row(*, seated_at: float = _NOW - 2000.0) -> dict:
    row = {
        "thread_id": "6928",
        "seated_at": seated_at,
        "registration_id": "reg-live",
    }
    return record_succession_claim(row, execution_id=_EXEC, now=_NOW)


def _stall_event(
    *,
    exec_id: str = _EXEC,
    sat_id: str | None = _SAT,
    seq: int = 100,
) -> dict:
    return {
        "seq": seq,
        "signal": "cdp.generate.stalled",
        "payload": {
            "execution_id": exec_id,
            "satellite_execution_id": sat_id,
            "stall_stage": "chip_missing",
            "error": "compose chip missing",
        },
    }


def _submit_event(*, exec_id: str = _EXEC, sat_id: str = _SAT, seq: int = 99) -> dict:
    return {
        "seq": seq,
        "signal": "cdp.generate.submitted",
        "payload": {
            "execution_id": exec_id,
            "satellite_execution_id": sat_id,
        },
    }


def test_stall_revokes_pending_succession_claim() -> None:
    """AC1/AC5: admit path → stall joins claim → revoked + failure recorded."""
    row = _pending_row()
    updated, action = apply_event_to_watch(row, _stall_event(), now=_NOW + 10.0)
    assert action == "revoked"
    assert updated["succession_status"] == "revoked"
    assert updated["revocation_count"] == 1
    assert updated["last_revoke"]["execution_id"] == _EXEC
    assert updated["seated_at"] == row["pre_hop_seated_at"]
    assert "successor_execution_id" not in updated


def test_stall_joins_via_satellite_id_after_submit() -> None:
    """AC1: join works from satellite id attached by submitted event."""
    row = _pending_row()
    with_sat, attach = apply_event_to_watch(row, _submit_event(), now=_NOW + 4.0)
    assert attach == "satellite_attached"
    stalled = _stall_event(exec_id="other-stargate", sat_id=_SAT)
    updated, action = apply_event_to_watch(with_sat, stalled, now=_NOW + 12.0)
    assert action == "revoked"


def test_observe_floor_constant_matches_sidecar() -> None:
    """AC2: floor stated from observed 4–14s chip_missing lag (15s minimum)."""
    assert STALL_OBSERVE_FLOOR_S >= 15.0
    row = _pending_row()
    pending = row["pending_succession"]
    assert pending["observe_floor_s"] == STALL_OBSERVE_FLOOR_S


def test_revoked_lane_respects_cooldown_on_scan(tmp_path: Path) -> None:
    """AC3: after revoke, evaluate_watch skips fire during cooldown (~30s scan)."""
    watch_path = tmp_path / "watches.json"
    row = _pending_row(seated_at=_NOW - 5000.0)
    revoked = revoke_succession_claim(
        row,
        stall_payload={"execution_id": _EXEC, "stall_stage": "chip_missing"},
        now=_NOW,
    )
    save_watches({"6928": revoked}, watch_path)
    decision = evaluate_watch(revoked, now=_NOW + 30.0, threshold=1500.0, cool=1800.0)
    assert decision.action == "skip"
    assert decision.reason == "cooldown"


def test_breaker_trips_after_n_revocations() -> None:
    """AC4: breaker at N repeated revocations."""
    row = _pending_row()
    for i in range(REVOKE_BREAKER_N):
        row = revoke_succession_claim(
            row,
            stall_payload={"execution_id": f"exec-{i}", "stall_stage": "chip_missing"},
            now=_NOW + i,
        )
    assert row["breaker_tripped"] is True
    assert breaker_blocks_hop(row) is True


def test_end_to_end_admit_submit_stall_revokes(tmp_path: Path) -> None:
    """AC5: reconstruct admit → submit → stall class end-to-end."""
    watch_path = tmp_path / "watches.json"
    state_path = tmp_path / "state.json"
    mark_hop_fired(
        "6928",
        now=_NOW,
        path=watch_path,
        execution_id=_EXEC,
        active_work_snap={
            "rows": [
                {
                    "execution_id": "exec-incumbent",
                    "registration_id": "reg-live",
                    "status": "running",
                }
            ]
        },
    )
    events = [
        {
            "seq": 1,
            "signal": "cdp.generate.admitted",
            "payload": {"execution_id": _EXEC, "thread_id": "6928"},
        },
        _submit_event(seq=2),
        _stall_event(seq=3),
    ]

    def _query(_sql: str, _params: list, _limit: int) -> list[dict]:
        return events

    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.emit_succession_revoked"
    ):
        result = reconcile_stall_revocations(
            watches_path=watch_path,
            state_path=state_path,
            now=_NOW + 14.0,
            query_fn=_query,
        )
    assert any(a["action"] == "revoked" for a in result["actions"])
    watches = load_watches(watch_path)
    assert watches["6928"]["succession_status"] == "revoked"


def test_live_control_a_not_revoked_without_stall(tmp_path: Path) -> None:
    """AC6: pending claim with no stall is not revoked by time alone."""
    watch_path = tmp_path / "watches.json"
    row = _pending_row(seated_at=_NOW - 7200.0)
    save_watches({"6928": row}, watch_path)
    # Simulate long observe window elapsed — no stall event applied.
    aged = dict(row)
    aged["pending_succession"] = {
        **row["pending_succession"],
        "claimed_at": _NOW - 7200.0,
    }
    updated, action = apply_event_to_watch(
        aged,
        _stall_event(exec_id="unrelated-exec"),
        now=_NOW,
    )
    assert action is None
    assert updated.get("succession_status") == "pending"
    confirmed = confirm_succession_claim(row, now=_NOW + 3600.0)
    assert confirmed["succession_status"] == "confirmed"


def test_proof_confirms_pending_claim_without_revoke() -> None:
    """Control A path: proof clears pending without revoke."""
    row = _pending_row()
    proof = {
        "seq": 50,
        "signal": "cdp.generate.proof",
        "payload": {"execution_id": _EXEC, "archive_uri": "cortex://x"},
    }
    updated, action = apply_event_to_watch(row, proof, now=_NOW + 300.0)
    assert action == "confirmed"
    assert updated["succession_status"] == "confirmed"
    assert "pending_succession" not in updated


def test_proof_observes_harvest_required_beyond_execution_id() -> None:
    """AC-2: proof_observes_harvest must gate confirm; id membership is not enough.

    Removing ``proof_observes_harvest`` from apply_event_to_watch's proof
    branch makes this test fail — a bare execution_id would confirm.
    """
    bare = {"execution_id": _EXEC}
    harvested = {"execution_id": _EXEC, "archive_uri": "cortex://notes/x.md"}
    assert proof_observes_harvest(bare) is False
    assert proof_observes_harvest(harvested) is True
    row = _pending_row()
    updated, action = apply_event_to_watch(
        row,
        {"seq": 51, "signal": "cdp.generate.proof", "payload": bare},
        now=_NOW + 300.0,
    )
    assert action is None
    assert updated.get("succession_status") == "pending"


def test_mark_hop_fired_records_pending_claim(tmp_path: Path) -> None:
    watch_path = tmp_path / "watches.json"
    save_watches(
        {
            "6928": {
                "thread_id": "6928",
                "seated_at": _NOW - 2000.0,
                "registration_id": "reg-live",
            }
        },
        watch_path,
    )
    snap = {
        "rows": [
            {
                "execution_id": "exec-incumbent",
                "registration_id": "reg-live",
                "status": "running",
            }
        ]
    }
    assert mark_hop_fired(
        "6928",
        now=_NOW,
        path=watch_path,
        execution_id=_EXEC,
        active_work_snap=snap,
    )
    row = load_watches(watch_path)["6928"]
    assert row["succession_status"] == "pending"
    assert row["pending_succession"]["execution_id"] == _EXEC
    assert row["pre_hop_seated_at"] == _NOW - 2000.0
    assert row["superseded_registration_id"] == "reg-live"
    assert row["superseded_execution_id"] == "exec-incumbent"


def test_confirm_calls_push_before_release() -> None:
    """G2: primary incumbent gets a stand-down paste before release on confirm."""
    from contextlib import ExitStack
    from unittest.mock import patch

    from services.git_integration_worker.cursor_auto.hop_cadence_predecessor import (
        PredecessorVerdict,
    )
    from services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile import (
        reconcile_succession_confirmations,
    )

    snap = {
        "admission_count": 2,
        "rows": [
            {
                "execution_id": "exec-successor",
                "registration_id": "reg-new",
                "status": "running",
                "purpose": "operator-proxy",
            },
            {
                "execution_id": "exec-incumbent",
                "registration_id": "reg-old",
                "status": "running",
                "purpose": "operator-proxy",
            },
        ],
    }
    watches = {
        "6928": {
            "thread_id": "6928",
            "registration_id": "reg-old",
            "successor_execution_id": "stargate-uuid",
            "pending_satellite_execution_id": "exec-successor",
            "superseded_registration_id": "reg-old",
            "superseded_execution_id": "exec-incumbent",
            "predecessor_verdict": PredecessorVerdict.INCUMBENT_RECORDED.value,
        }
    }
    call_order: list[str] = []

    def _push(**kwargs: object) -> dict[str, bool]:
        call_order.append("push")
        return {"attempted": True, "ok": True}

    def _release(
        handle: object, idle_streak: int = 0, **kwargs: object
    ) -> dict[str, str]:
        call_order.append("release")
        return {"action": "deferred", "reason": "predecessor_idle_streak_unsatisfied"}

    stack = ExitStack()
    stack.enter_context(
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.load_watches",
            side_effect=lambda path=None: watches,
        )
    )
    stack.enter_context(
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.save_watches",
            side_effect=lambda data, path=None: watches.update(data) or None,
        )
    )
    stack.enter_context(
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.push_predecessor_receipt",
            side_effect=_push,
        )
    )
    stack.enter_context(
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.emit_succession_confirmed",
        )
    )
    stack.enter_context(
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.emit_registration_advanced",
        )
    )
    stack.enter_context(
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.emit_seat_rebound",
        )
    )

    with stack:
        result = reconcile_succession_confirmations(
            snapshot_reader=lambda: snap,
            release_fn=_release,
        )

    assert len(result["confirmations"]) == 1
    assert call_order == ["push", "release"]
