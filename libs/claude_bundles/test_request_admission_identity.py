"""Identity-on-gate bind — server authority before lease refuse."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from claude_bundles.request_admission_identity import (
    gate_request_admission,
    get_identity_counters,
    load_active_work_snap,
    load_active_work_snap_result,
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
    with (
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={"7188": {"registration_id": "5420b367-watch"}},
        ),
        patch(
            "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
            return_value="5420b367-origin",
        ),
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
    with (
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={"7188": {"registration_id": "5420b367-watch"}},
        ),
        patch(
            "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
            return_value=None,
        ),
    ):
        identity = resolve_request_admission_identity(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap=snap,
        )
    assert identity.source == "single_seat_active_work"
    assert identity.registration_id == "5420b367-holder"


def test_unresolvable_on_watch_lane_without_bind_sources():
    with (
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={"7188": {"thread_id": "7188"}},
        ),
        patch(
            "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
            return_value=None,
        ),
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
    with (
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={"7188": {"thread_id": "7188"}},
        ),
        patch(
            "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
            return_value=None,
        ),
    ):
        refusal = gate_request_admission(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap={"rows": []},
        )
    assert refusal is None
    counters = get_identity_counters()
    assert counters["unresolvable_on_watch_lane"] == 1


def test_load_active_work_snap_failed_get_is_distinguishable_from_empty():
    with patch(
        "cdp_ask.client.CdpAskClient",
        side_effect=RuntimeError("active-work unavailable"),
    ):
        failed = load_active_work_snap()
    assert failed == {}
    assert get_identity_counters()["active_work_snap_load_failed"] == 1

    reset_identity_counters_for_tests()
    mock_client = MagicMock()
    mock_client._request.return_value = {}
    with patch("cdp_ask.client.CdpAskClient", return_value=mock_client):
        empty = load_active_work_snap()
    assert empty == {}
    assert "active_work_snap_load_failed" not in get_identity_counters()


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
        refusal = gate_request_admission(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap=snap,
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
        refusal = gate_request_admission(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap=snap,
        )
    assert refusal is not None
    assert refusal["code"] == "seat.lease_lost"
    data = refusal["data"]
    assert data["superseded_registration_id"] == "5420b367-old"
    assert data["identity_source"] == "single_seat_active_work"


def test_watch_row_is_not_admission_bind():
    with (
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={"7188": {"registration_id": "5420b367-watch-only"}},
        ),
        patch(
            "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
            return_value=None,
        ),
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
    with (
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={"7188": row},
        ),
        patch(
            "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
            return_value="5420b367-old",
        ),
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

    with (
        patch(
            "tools.agent_bus.request._resolve_hop_seat_request_refusal",
            return_value={
                "code": "seat.lease_lost",
                "source": "rpc",
                "retryable": False,
                "data": {},
            },
        ),
        patch(
            "tools.agent_bus.request._request_impl",
        ) as impl_mock,
    ):
        result = _request_dispatch(
            thread="7188",
            subject="s",
            body="b",
            from_agent="web-anthropic",
        )
    impl_mock.assert_not_called()
    assert result.get("code") == "seat.lease_lost"


def test_identity_gated_emits_on_both_outcomes_and_unwatched():
    """AC1: dual-arm — fail if emit re-gated on watch-only or reject-only."""
    row = {
        "registration_id": "5420b367-new",
        "superseded_registration_id": "5420b367-old",
        "successor_execution_id": "exec-new",
        "pending_satellite_execution_id": "sat-live",
    }
    refuse_snap = {
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
    admit_snap = {
        "rows": [
            {
                "execution_id": "sat-live",
                "registration_id": "5420b367-new",
                "parent_thread": "7199",
                "purpose": "operator-proxy",
                "status": "running",
            }
        ]
    }
    with patch(
        "claude_bundles.request_admission_identity._emit_identity_gated"
    ) as emit_mock:
        with (
            patch(
                "claude_bundles.hop_seat_cutover.load_watches",
                return_value={"7188": {"thread_id": "7188"}},
            ),
            patch(
                "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
                return_value=None,
            ),
            patch(
                "claude_bundles.request_admission_identity.load_active_work_snap_result",
                return_value=({"rows": []}, False),
            ),
        ):
            gate_request_admission(thread_id="7188", caller_registration_id=None)

        with (
            patch(
                "claude_bundles.hop_seat_cutover.load_watches",
                return_value={},
            ),
            patch(
                "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
                return_value=None,
            ),
            patch(
                "claude_bundles.request_admission_identity.load_active_work_snap_result",
                return_value=(admit_snap, False),
            ),
        ):
            gate_request_admission(thread_id="7199", caller_registration_id=None)

        with (
            patch(
                "claude_bundles.hop_seat_cutover.load_watches",
                return_value={"7188": row},
            ),
            patch(
                "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
                return_value=None,
            ),
            patch(
                "claude_bundles.request_admission_identity.load_active_work_snap_result",
                return_value=(refuse_snap, False),
            ),
        ):
            gate_request_admission(thread_id="7188", caller_registration_id=None)

    assert emit_mock.call_count == 3
    outcomes = [c.kwargs["outcome"] for c in emit_mock.call_args_list]
    assert outcomes == ["admit", "admit", "reject"]
    watch_flags = [
        c.kwargs["identity"].watch_present for c in emit_mock.call_args_list
    ]
    assert watch_flags == [True, False, True]


def test_unresolvable_reason_missing_thread_id():
    identity = resolve_request_admission_identity(
        thread_id=None,
        caller_registration_id=None,
        active_work_snap={"rows": []},
    )
    assert identity.source == "unresolvable"
    assert identity.unresolvable_reason == "missing_thread_id"


def test_unresolvable_reason_snap_load_failed():
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"7188": {"thread_id": "7188"}},
    ):
        identity = resolve_request_admission_identity(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap={},
            snap_load_failed=True,
        )
    assert identity.source == "unresolvable"
    assert identity.unresolvable_reason == "snap_load_failed"


def test_unresolvable_reason_empty_snap():
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"7188": {"thread_id": "7188"}},
    ):
        identity = resolve_request_admission_identity(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap={"rows": []},
            snap_load_failed=False,
        )
    assert identity.unresolvable_reason == "empty_snap"


def test_unresolvable_reason_zero_matches():
    snap = {
        "rows": [
            {
                "execution_id": "other-lane",
                "registration_id": "5420b367-other",
                "parent_thread": "9999",
                "purpose": "operator-proxy",
                "status": "running",
            }
        ]
    }
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"7188": {"thread_id": "7188"}},
    ):
        identity = resolve_request_admission_identity(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap=snap,
        )
    assert identity.unresolvable_reason == "zero_matches"


def test_unresolvable_reason_ambiguous_matches():
    snap = {
        "rows": [
            {
                "execution_id": "a",
                "registration_id": "5420b367-a",
                "parent_thread": "7188",
                "purpose": "operator-proxy",
                "status": "running",
            },
            {
                "execution_id": "b",
                "registration_id": "5420b367-b",
                "parent_thread": "7188",
                "purpose": "operator-proxy",
                "status": "running",
            },
        ]
    }
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"7188": {"thread_id": "7188"}},
    ):
        identity = resolve_request_admission_identity(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap=snap,
        )
    assert identity.unresolvable_reason == "ambiguous_matches"


def test_gate_snap_load_failed_emits_reason_not_empty_snap():
    with (
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={"7188": {"thread_id": "7188"}},
        ),
        patch(
            "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
            return_value=None,
        ),
        patch(
            "claude_bundles.request_admission_identity.load_active_work_snap_result",
            return_value=({}, True),
        ),
        patch(
            "claude_bundles.request_admission_identity._emit_identity_gated"
        ) as emit_mock,
    ):
        refusal = gate_request_admission(
            thread_id="7188",
            caller_registration_id=None,
        )
    assert refusal is None
    emit_mock.assert_called_once()
    identity = emit_mock.call_args.kwargs["identity"]
    assert identity.unresolvable_reason == "snap_load_failed"


def test_load_active_work_snap_result_failed_flag():
    with patch(
        "cdp_ask.client.CdpAskClient",
        side_effect=RuntimeError("active-work unavailable"),
    ):
        snap, failed = load_active_work_snap_result()
    assert snap == {}
    assert failed is True


def test_mirror_to_event_service_includes_iso_timestamp():
    import json as json_mod
    from unittest.mock import MagicMock

    from claude_bundles.hop_cadence_lease_events import (
        GiwCursorAutoHopCadenceIdentityBound,
        _mirror_to_event_service,
    )

    sent: list[bytes] = []
    mock_sock = MagicMock()
    mock_sock.sendall = lambda data: sent.append(data)

    with patch("socket.socket") as socket_cls:
        socket_cls.return_value.__enter__.return_value = mock_sock
        _mirror_to_event_service(
            GiwCursorAutoHopCadenceIdentityBound(
                thread_id="7188",
                identity_source="single_seat_active_work",
                watch_present=True,
                registration_id="5420b367-test",
            )
        )

    payload = json_mod.loads(sent[0].decode())
    assert payload["timestamp"]
    assert "T" in payload["timestamp"]
    assert payload["ts_unix_ms"] > 0
