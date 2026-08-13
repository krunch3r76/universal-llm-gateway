"""Identity-on-gate bind — server authority before lease refuse."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from claude_bundles.request_admission_identity import (
    gate_request_admission,
    get_identity_counters,
    observe_identity_on_gate,
    reset_identity_counters_for_tests,
    resolve_request_admission_identity,
)


@pytest.fixture(autouse=True)
def _reset_counters() -> None:
    reset_identity_counters_for_tests()


def test_resolve_caller_supplied_wins_over_watch():
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"7188": {"registration_id": "5420b367-watch"}},
    ):
        identity = resolve_request_admission_identity(
            thread_id="7188",
            caller_registration_id="5420b367-caller",
        )
    assert identity.source == "caller_supplied"
    assert identity.registration_id == "5420b367-caller"
    assert identity.watch_present is True


def test_resolve_from_origin_cse_when_caller_omits_registration():
    snap = {"rows": []}
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"7188": {"registration_id": "5420b367-watch"}},
    ), patch(
        "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
        return_value="5420b367-origin",
    ):
        identity = resolve_request_admission_identity(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap=snap,
        )
    assert identity.source == "origin_cse"
    assert identity.registration_id == "5420b367-origin"
    assert identity.watch_present is True


def test_resolve_single_seat_active_work_when_empty_wire():
    snap = {
        "rows": [
            {
                "execution_id": "exec-holder",
                "registration_id": "5420b367-holder",
                "parent_thread": "7188",
                "purpose": "operator-proxy",
                "status": "running",
            }
        ]
    }
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"7188": {"registration_id": "5420b367-watch"}},
    ), patch(
        "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
        return_value=None,
    ):
        identity = resolve_request_admission_identity(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap=snap,
        )
    assert identity.source == "single_seat_active_work"
    assert identity.registration_id == "5420b367-holder"


def test_unresolvable_on_watch_lane_without_bind_sources():
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"7188": {"thread_id": "7188"}},
    ), patch(
        "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
        return_value=None,
    ):
        identity = resolve_request_admission_identity(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap={"rows": []},
        )
    assert identity.source == "unresolvable"
    assert identity.registration_id is None
    assert identity.watch_present is True


def test_gate_refuses_unresolvable_on_watched_lane():
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"7188": {"thread_id": "7188"}},
    ), patch(
        "claude_bundles.request_admission_identity.load_active_work_snap",
        return_value={"rows": []},
    ), patch(
        "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
        return_value=None,
    ):
        refusal = gate_request_admission(
            thread_id="7188",
            caller_registration_id=None,
        )
    assert refusal is not None
    assert refusal["code"] == "seat.lease_lost"
    assert refusal["source"] == "rpc"
    assert refusal["retryable"] is False
    assert refusal["data"]["identity_source"] == "unresolvable"


def test_gate_admits_holder_via_single_seat_bind_without_wire_id():
    row = {
        "registration_id": "5420b367-new",
        "superseded_registration_id": "5420b367-old",
        "successor_execution_id": "exec-new",
        "pending_satellite_execution_id": "sat-live",
    }
    snap = {
        "rows": [
            {
                "execution_id": "sat-live",
                "registration_id": "5420b367-new",
                "parent_thread": "7188",
                "purpose": "operator-proxy",
                "status": "running",
            }
        ]
    }
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"7188": row},
    ), patch(
        "claude_bundles.request_admission_identity.load_active_work_snap",
        return_value=snap,
    ), patch(
        "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
        return_value=None,
    ):
        refusal = gate_request_admission(
            thread_id="7188",
            caller_registration_id=None,
        )
    assert refusal is None


def test_gate_refuses_predecessor_bound_via_single_seat_active_work():
    row = {
        "registration_id": "5420b367-new",
        "superseded_registration_id": "5420b367-old",
        "successor_execution_id": "exec-new",
        "pending_satellite_execution_id": "sat-live",
    }
    snap = {
        "rows": [
            {
                "execution_id": "sat-live",
                "registration_id": "5420b367-old",
                "parent_thread": "7188",
                "purpose": "operator-proxy",
                "status": "running",
            }
        ]
    }
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"7188": row},
    ), patch(
        "claude_bundles.request_admission_identity.load_active_work_snap",
        return_value=snap,
    ), patch(
        "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
        return_value=None,
    ):
        refusal = gate_request_admission(
            thread_id="7188",
            caller_registration_id=None,
        )
    assert refusal is not None
    assert refusal["code"] == "seat.lease_lost"
    data = refusal["data"]
    assert data["superseded_registration_id"] == "5420b367-old"
    assert data["identity_source"] == "single_seat_active_work"


def test_watch_row_is_not_admission_bind():
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"7188": {"registration_id": "5420b367-watch-only"}},
    ), patch(
        "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
        return_value=None,
    ):
        identity = resolve_request_admission_identity(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap={"rows": []},
        )
    assert identity.source == "unresolvable"
    assert identity.registration_id is None


def test_counterfactual_refuse_when_bound_predecessor_after_confirm():
    row = {
        "superseded_registration_id": "5420b367-old",
        "successor_execution_id": "exec-new",
        "pending_satellite_execution_id": "sat-live",
    }
    snap = {
        "rows": [
            {
                "execution_id": "sat-live",
                "registration_id": "reg-new",
                "status": "running",
            }
        ]
    }
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"7188": row},
    ), patch(
        "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
        return_value="5420b367-old",
    ):
        observe_identity_on_gate(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap=snap,
        )
    counters = get_identity_counters()
    assert counters["identity_source:origin_cse"] == 1
    assert counters["counterfactual_would_refuse"] == 1


def test_request_dispatch_uses_gate_before_impl():
    import sys
    from pathlib import Path

    mcp_root = Path(__file__).resolve().parents[2] / "services" / "mcp-server"
    sys.path.insert(0, str(mcp_root))
    from tools.agent_bus.request import _request_dispatch

    with patch(
        "tools.agent_bus.request._resolve_hop_seat_request_refusal",
        return_value={"code": "seat.lease_lost", "source": "rpc", "retryable": False, "data": {}},
    ), patch(
        "tools.agent_bus.request._request_impl",
    ) as impl_mock:
        result = _request_dispatch(
            thread="7188",
            subject="s",
            body="b",
            from_agent="web-anthropic",
        )
    impl_mock.assert_not_called()
    assert result.get("code") == "seat.lease_lost"
