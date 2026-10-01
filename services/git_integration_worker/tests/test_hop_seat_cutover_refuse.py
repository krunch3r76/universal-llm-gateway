"""Hop seat cutover refuse-at-request — cadence repeat + predecessor request gate."""

from __future__ import annotations

import time
from unittest.mock import patch

from claude_bundles.hop_seat_cutover import (
    refuse_cadence_hop_for_live_seat,
    resolve_request_refusal,
    successor_confirm_active,
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
        ],
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




def test_refuse_cadence_hop_while_same_registration_still_running():
    row = {
        "registration_id": "reg-incumbent",
        "last_hop_at": time.time() - 60.0,
    }
    refuse, reason, evidence = refuse_cadence_hop_for_live_seat(
        row, _snap(registration_id="reg-incumbent")
    )
    assert refuse is True
    assert reason == "seat_live_refuse_at_request"
    assert evidence["registration_id"] == "reg-incumbent"


def test_first_cadence_hop_allowed_while_seat_live():
    row = {"registration_id": "reg-incumbent", "last_hop_at": None}
    refuse, reason, _ = refuse_cadence_hop_for_live_seat(
        row, _snap(registration_id="reg-incumbent")
    )
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
    with (
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={"7188": row},
        ),
        patch(
            "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
            return_value=None,
        ),
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










