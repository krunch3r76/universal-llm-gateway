"""Hop seat cutover refuse-at-request — cadence repeat + predecessor request gate."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_bundles.hop_seat_cutover import (
    effective_seated_at_after_hop,
    refuse_cadence_hop_for_live_seat,
    resolve_request_refusal,
    successor_confirm_active,
)

from services.git_integration_worker.cursor_auto.hop_cadence import (
    build_cadence_hop_body,
    fire_hop_for_decision,
)
from services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile import (
    reconcile_succession_confirmations,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    HopDecision,
    advance_registration_on_confirm,
    evaluate_watch,
)


def _snap(*, registration_id: str, execution_id: str = "exec-successor") -> dict:
    return {
        "free_slots": 1,
        "running_count": 1,
        "at_soft_limit": False,
        "at_hard_limit": False,
        "rows": [
            {
                "execution_id": execution_id,
                "registration_id": registration_id,
                "status": "running",
                "purpose": "operator-proxy",
            }
        ]
    }


def test_confirm_active_join_keys_intersection():
    row = {
        "successor_execution_id": "stargate-uuid",
        "pending_satellite_execution_id": "satellite-hex",
    }
    empty_snap = {"rows": []}
    assert successor_confirm_active(row, empty_snap) is False

    live_snap = _snap(registration_id="reg-new", execution_id="satellite-hex")
    assert successor_confirm_active(row, live_snap) is True


def test_confirm_active_multi_key_partial_live():
    row = {
        "successor_execution_id": "stargate-only",
        "pending_satellite_execution_id": "satellite-live",
    }
    snap = _snap(registration_id="reg-new", execution_id="satellite-live")
    assert successor_confirm_active(row, snap) is True

    stargate_only_snap = _snap(registration_id="reg-new", execution_id="stargate-only")
    assert successor_confirm_active(row, stargate_only_snap) is True


def test_confirm_false_when_membership_empty_despite_pending_keys():
    row = {
        "successor_execution_id": "stargate-uuid",
        "pending_satellite_execution_id": "satellite-pending",
        "pending_execution_id": "stargate-uuid",
    }
    snap = {"rows": []}
    assert successor_confirm_active(row, snap) is False


def test_effective_seated_at_prefers_post_hop_reset_over_stale_registry():
    """Would fail before fix: registry started_at kept age above threshold after hop."""
    now = 2_000_000.0
    row = {
        "thread_id": "6885",
        "registration_id": "reg-live",
        "seated_at": now - 100.0,
        "last_hop_at": now - 2000.0,
    }

    def _registry_started(_reg: str | None) -> float | None:
        return now - 5_000.0

    seated = effective_seated_at_after_hop(row, registry_started_at=_registry_started)
    assert seated == now - 100.0
    decision = evaluate_watch(row, now=now, threshold=1500.0, cool=1800.0)
    assert decision.action == "skip"
    assert decision.reason == "below_threshold"


def test_refuse_cadence_hop_while_same_registration_still_running():
    row = {
        "registration_id": "reg-incumbent",
        "last_hop_at": time.time() - 60.0,
    }
    refuse, reason, evidence = refuse_cadence_hop_for_live_seat(row, _snap(registration_id="reg-incumbent"))
    assert refuse is True
    assert reason == "seat_live_refuse_at_request"
    assert evidence["registration_id"] == "reg-incumbent"


def test_first_cadence_hop_allowed_while_seat_live():
    row = {"registration_id": "reg-incumbent", "last_hop_at": None}
    refuse, reason, _ = refuse_cadence_hop_for_live_seat(row, _snap(registration_id="reg-incumbent"))
    assert refuse is False
    assert reason is None


def test_resolve_request_refusal_after_successor_confirm():
    row = {
        "superseded_registration_id": "reg-old",
        "successor_execution_id": "stargate-uuid",
        "pending_satellite_execution_id": "satellite-live",
    }
    snap = _snap(registration_id="reg-new", execution_id="satellite-live")
    refusal = resolve_request_refusal(
        thread_id="6885",
        cse_registration_id="reg-old",
        snap=snap,
        path=None,
    )
    assert refusal is None

    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"6885": row},
    ):
        refusal = resolve_request_refusal(
            thread_id="6885",
            cse_registration_id="reg-old",
            snap=snap,
        )
    assert refusal is not None
    assert refusal["code"] == "seat.lease_lost"
    data = refusal["data"]
    assert data["reason"] == "superseded_predecessor_refuse_at_request"
    assert data["successor_execution_id"] == "stargate-uuid"
    assert data["successor_satellite_execution_id"] == "satellite-live"
    assert data["signal"] == "cdp_ask_active_work_membership"


def test_resolve_request_refusal_admits_holder_and_self_supersede():
    """Current holder always admits; self-supersede poison rows do not refuse."""
    snap = _snap(registration_id="reg-live", execution_id="satellite-live")
    holder_row = {
        "registration_id": "reg-live",
        "superseded_registration_id": "reg-old",
        "successor_execution_id": "stargate-uuid",
        "pending_satellite_execution_id": "satellite-live",
    }
    poison_row = {
        "registration_id": "reg-live",
        "superseded_registration_id": "reg-live",
        "successor_execution_id": "stargate-uuid",
        "pending_satellite_execution_id": "satellite-live",
    }
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"6885": holder_row},
    ):
        assert (
            resolve_request_refusal(
                thread_id="6885",
                cse_registration_id="reg-live",
                snap=snap,
            )
            is None
        )
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"6885": poison_row},
    ):
        assert (
            resolve_request_refusal(
                thread_id="6885",
                cse_registration_id="reg-live",
                snap=snap,
            )
            is None
        )



def test_i4_predecessor_refused_15s_after_confirm_holder_readmits():
    """I4 verbatim AC: bound predecessor refused; holder re-issue admits empty wire."""
    row = {
        "registration_id": "reg-new",
        "superseded_registration_id": "reg-old",
        "successor_execution_id": "stargate-uuid",
        "pending_satellite_execution_id": "satellite-live",
        "succession_confirmed_at": time.time() - 15.0,
    }
    snap = _snap(registration_id="reg-new", execution_id="satellite-live")
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"7188": row},
    ), patch(
        "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
        return_value=None,
    ):
        from claude_bundles.request_admission_identity import gate_request_admission

        predecessor = gate_request_admission(
            thread_id="7188",
            caller_registration_id="reg-old",
            active_work_snap=snap,
        )
        assert predecessor is not None
        assert predecessor["code"] == "seat.lease_lost"
        for key in ("code", "message", "source", "retryable", "data"):
            assert key in predecessor

        holder = gate_request_admission(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap={
                "rows": [
                    {
                        "execution_id": "satellite-live",
                        "registration_id": "reg-new",
                        "parent_thread": "7188",
                        "purpose": "operator-proxy",
                        "status": "running",
                    }
                ]
            },
        )
        assert holder is None


def test_resolve_request_refusal_envelope_protocol_error_shape():
    row = {
        "superseded_registration_id": "reg-old",
        "successor_execution_id": "exec-new",
    }
    snap = _snap(registration_id="reg-new", execution_id="exec-new")
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"6885": row},
    ):
        refusal = resolve_request_refusal(
            thread_id="6885",
            cse_registration_id="reg-old",
            snap=snap,
        )
    assert refusal is not None
    for key in ("code", "message", "source", "retryable", "data"):
        assert key in refusal
    assert refusal["code"] == "seat.lease_lost"
    assert refusal["source"] == "rpc"
    assert refusal["retryable"] is False
    data = refusal["data"]
    assert data["thread_id"] == "6885"
    assert data["superseded_registration_id"] == "reg-old"
    assert data["successor_execution_id"] == "exec-new"
    assert data["signal"] == "cdp_ask_active_work_membership"


def test_build_cadence_hop_body_superseded_registration_id_line():
    decision = HopDecision(
        "6885",
        "fire",
        "age_threshold_met",
        age_s=2000.0,
        threshold_s=1500.0,
    )
    body = build_cadence_hop_body(decision, registration_id="reg-incumbent")
    lines = body.splitlines()
    assert any(line == "superseded_registration_id: reg-incumbent" for line in lines)
    assert any(line == "parent_thread: 6885" for line in lines)
    assert any(line.startswith("you_are:") for line in lines)
    assert not any(line.startswith("registration_id:") for line in lines)
    birth_lines = [line for line in lines if line.startswith("successor_birth_id:")]
    assert len(birth_lines) == 1
    assert birth_lines[0].split(":", 1)[1].strip()


def test_registration_advanced_once_on_confirm():
    row = {
        "thread_id": "6885",
        "registration_id": "reg-old",
        "successor_execution_id": "stargate-uuid",
        "pending_satellite_execution_id": "satellite-live",
        "superseded_registration_id": "reg-old",
        "superseded_execution_id": "exec-incumbent-old",
        "predecessor_verdict": "incumbent_recorded",
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
        assert confirmed_mock.call_count == 1
        assert advanced_mock.call_count == 1
        advanced_kwargs = advanced_mock.call_args.kwargs
        assert advanced_kwargs["prior_registration_id"] == "reg-old"
        assert advanced_kwargs["new_registration_id"] == "reg-new"
        assert advanced_kwargs["superseding_execution_id"] == "satellite-live"
        assert advanced_kwargs["superseded_execution_id"] == "exec-incumbent-old"
        assert watches["6885"]["registration_id"] == "reg-new"
        assert watches["6885"]["succession_confirm_record"]["prior_registration_id"] == "reg-old"

        confirmed_mock.reset_mock()
        advanced_mock.reset_mock()
        result2 = reconcile_succession_confirmations(snapshot_reader=lambda: snap)
        assert result2["confirmations"] == []
        assert confirmed_mock.call_count == 0
        assert advanced_mock.call_count == 0


def test_confirm_posts_seat_registration_stamp_echoing_birth_id():
    birth = "d" * 32
    row = {
        "thread_id": "6885",
        "registration_id": "reg-old",
        "successor_execution_id": "stargate-uuid",
        "pending_satellite_execution_id": "satellite-live",
        "superseded_registration_id": "reg-old",
        "superseded_execution_id": "exec-incumbent-old",
        "predecessor_verdict": "incumbent_recorded",
        "successor_birth_id": birth,
    }
    watches = {"6885": dict(row)}
    snap = _snap(registration_id="reg-new", execution_id="satellite-live")
    posted: list[tuple[str, str]] = []

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
        reconcile_succession_confirmations(
            snapshot_reader=lambda: snap,
            stamp_poster=lambda thread_id, body: posted.append((thread_id, body)),
        )
    assert len(posted) == 1
    thread_id, stamp = posted[0]
    assert thread_id == "6885"
    assert stamp.startswith("TYPE: SEAT_REGISTRATION\n")
    assert f"successor_birth_id: {birth}" in stamp
    assert "registration_id: reg-new" in stamp
    assert "execution_id: satellite-live" in stamp


def test_advance_registration_on_confirm_unit():
    row = {
        "registration_id": "reg-old",
        "superseded_execution_id": "exec-incumbent",
    }
    aw_row = {
        "registration_id": "reg-new",
        "execution_id": "exec-1",
        "status": "running",
        "chat_url": "https://claude.ai/cowork/cse_successor",
    }
    updated, transition = advance_registration_on_confirm(
        row,
        matched_key="exec-1",
        active_work_row=aw_row,
        now=time.time(),
        prior_registration_id="reg-old",
    )
    assert transition == ("reg-old", "reg-new")
    assert updated["registration_id"] == "reg-new"
    assert updated["chat_url"] == "https://claude.ai/cowork/cse_successor"
    updated2, transition2 = advance_registration_on_confirm(
        updated,
        matched_key="exec-1",
        active_work_row=aw_row,
        now=time.time(),
        prior_registration_id="reg-old",
    )
    assert transition2 is None
    assert updated2["registration_id"] == "reg-new"


@pytest.mark.asyncio
async def test_fire_hop_refuses_repeat_while_registration_streams():
    """Fails before change: cadence re-commissions against a live incumbent seat."""
    decision = HopDecision(
        "6885",
        "fire",
        "age_threshold_met",
        age_s=2000.0,
        threshold_s=1500.0,
    )
    row = {
        "registration_id": "reg-live",
        "last_hop_at": time.time() - 100.0,
        "from_agent": "web-anthropic",
    }
    queue = MagicMock()
    job = MagicMock(job_id="job-hop")
    queue.enqueue.return_value = job
    snap = _snap(registration_id="reg-live")

    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence.read_cdp_lane_snapshot",
        return_value=snap,
    ), patch(
        "services.git_integration_worker.cursor_auto.hop_cadence.run_continuity_hop_concurrent",
        new_callable=AsyncMock,
        return_value={"ok": True, "execution_id": "exec-new"},
    ), patch(
        "services.git_integration_worker.cursor_auto.hop_cadence.emit_cadence_refuse",
    ):
        outcome = await fire_hop_for_decision(
            decision,
            queue=queue,
            row=row,
            snapshot_reader=lambda: snap,
        )

    assert outcome["ok"] is False
    assert outcome["reason"] == "seat_live_refuse_at_request"
    queue.enqueue.assert_not_called()
