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
            return_value={"7188": {"thread_id": "7188"}},
        ),
        patch(
            "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
            return_value="5420b367-origin",
        ),
        patch(
            "claude_bundles.request_admission_resume._resolve_bus_cse_registration",
            return_value=None,
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
        patch(
            "claude_bundles.request_admission_resume._resolve_bus_cse_registration",
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


def test_census_n1_from_seated_rows_only_operator_proxy():
    """AC2: a single listable driving operator row (no execution-store row) is N=1."""
    snap = {
        "rows": [],
        "seated_rows": [
            {
                "execution_id": "__none:seated_no_stream__",
                "registration_id": "driving-root",
                "parent_thread": "9497",
                "purpose": "operator-proxy",
                "status": "running",
                "source": "cse-session-registry",
            }
        ],
    }
    with (
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={"9497": {"thread_id": "9497"}},
        ),
        patch(
            "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
            return_value=None,
        ),
    ):
        identity = resolve_request_admission_identity(
            thread_id="9497",
            caller_registration_id=None,
            active_work_snap=snap,
        )
    assert identity.source == "single_seat_active_work"
    assert identity.census_n == 1
    assert identity.registration_id == "driving-root"
    assert identity.match_registration_ids == ("driving-root",)


def test_census_counts_listable_hop_on_matching_parent() -> None:
    """AC1: admission census has no mission_kind filter — live hop is N=1."""
    from claude_bundles.hop_cadence_seat_snap import seated_row_from_registry_record
    from claude_bundles.request_admission_census import census_match_ids

    projected = seated_row_from_registry_record(
        {
            "registration_id": "6db8df9ce3e94b46851a544f558af247",
            "status": "active",
            "purpose": "operator-proxy",
            "parent_thread": "9506",
            "mission_kind": "hop",
        }
    )
    assert projected is not None
    snap = {"rows": [], "seated_rows": [projected]}
    assert census_match_ids("9506", snap) == ["6db8df9ce3e94b46851a544f558af247"]
    assert census_match_ids("9497", snap) == []


def test_census_hop_plus_driving_is_n2() -> None:
    """AC1 ruling: hop+driving overlap stays N=2 — no pick-one."""
    from claude_bundles.request_admission_census import census_match_ids

    snap = {
        "rows": [],
        "seated_rows": [
            {
                "registration_id": "hop-row",
                "parent_thread": "9506",
                "purpose": "operator-proxy",
                "status": "running",
            },
            {
                "registration_id": "root-row",
                "parent_thread": "9506",
                "purpose": "operator-proxy",
                "status": "running",
            },
        ],
    }
    matches = census_match_ids("9506", snap)
    assert sorted(matches) == ["hop-row", "root-row"]


def test_gate_refuses_empty_snap_on_watched_lane():
    """AC3: empty_snap is N=0 — refuse at enqueue, do not fail-open."""
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
            "claude_bundles.request_admission_resume._resolve_bus_cse_registration",
            return_value=None,
        ),
    ):
        refusal = gate_request_admission(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap={"rows": []},
        )
    assert refusal is not None
    assert refusal["code"] == "seat.identity_unresolvable"
    assert refusal["data"]["reason"] == "empty_snap"
    assert refusal["data"]["census_n"] == 0
    counters = get_identity_counters()
    assert counters["census_refuse:empty_snap"] == 1
    assert "unresolvable_on_watch_lane" not in counters


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


def test_gate_admits_self_supersede_poison_row_via_single_seat_bind():
    """Live 9031/9345 shape: registration_id == superseded_registration_id ⇒ admit."""
    same = "6d272f276c674ffb94ef1489335ab482"
    sat = "45aff9ccfece4024be6650fa0a15e75b"
    row = {
        "registration_id": same,
        "superseded_registration_id": same,
        "superseded_execution_id": sat,
        "successor_execution_id": "03908796-2e45-4a42-bce8-22b997117655",
        "pending_satellite_execution_id": sat,
        "pending_succession": {
            "execution_id": "03908796-2e45-4a42-bce8-22b997117655",
            "satellite_execution_id": sat,
            "claimed_at": 1_786_972_553.0,
            "join_max_age_s": 600.0,
        },
    }
    snap = {
        "rows": [
            {
                "execution_id": sat,
                "registration_id": same,
                "parent_thread": "9031",
                "purpose": "operator-proxy",
                "status": "running",
            }
        ]
    }
    with (
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={"9031": row},
        ),
        patch(
            "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
            return_value=None,
        ),
    ):
        refusal = gate_request_admission(
            thread_id="9031",
            caller_registration_id=None,
            active_work_snap=snap,
        )
    assert refusal is None


def test_n0_watch_reg_admits_watch_resume():
    """AC-R1: N=0 + watch.reg → admit via watch_resume after terminal."""
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
    assert identity.source == "watch_resume"
    assert identity.registration_id == "5420b367-watch-only"
    assert identity.watch_present is True


def test_n1_watch_ignored_when_census_has_row():
    """AC-R3: continue-while-running — watch ignored when census_n==1."""
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
            active_work_snap=snap,
        )
    assert identity.source == "single_seat_active_work"
    assert identity.registration_id == "5420b367-holder"
    assert identity.census_n == 1


def test_n0_mailbox_resume_when_thread_watch_misses():
    """AC-R4: mailbox home-lane watch when watch[thread] misses."""
    with (
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={
                "9473": {"registration_id": "5420b367-mailbox"},
            },
        ),
        patch(
            "claude_bundles.request_admission_resume._resolve_bus_cse_registration",
            return_value=None,
        ),
        patch(
            "claude_bundles.request_admission_resume._resolve_origin_cse_registration",
            return_value=None,
        ),
    ):
        identity = resolve_request_admission_identity(
            thread_id="9551",
            caller_registration_id=None,
            from_agent="cdp-operator-9473-proxy",
            active_work_snap={"rows": []},
        )
    assert identity.source == "mailbox_resume"
    assert identity.registration_id == "5420b367-mailbox"


def test_n0_cse_resume_when_watch_and_mailbox_miss():
    """AC-R4: bus CSE association when watch/mailbox miss."""
    with (
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={},
        ),
        patch(
            "claude_bundles.request_admission_resume._resolve_bus_cse_registration",
            return_value="5420b367-cse-bus",
        ),
        patch(
            "claude_bundles.request_admission_resume._resolve_origin_cse_registration",
            return_value=None,
        ),
    ):
        identity = resolve_request_admission_identity(
            thread_id="7188",
            caller_registration_id=None,
            from_agent="web-anthropic",
            active_work_snap={"rows": []},
        )
    assert identity.source == "cse_resume"
    assert identity.registration_id == "5420b367-cse-bus"


def test_n0_never_binds_superseded_registration_id_from_watch():
    """AC-R4: superseded_registration_id is never admission identity."""
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={
            "7188": {
                "registration_id": "5420b367-current",
                "superseded_registration_id": "5420b367-old",
            }
        },
    ):
        identity = resolve_request_admission_identity(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap={"rows": []},
        )
    assert identity.source == "watch_resume"
    assert identity.registration_id == "5420b367-current"
    assert identity.registration_id != "5420b367-old"


def test_watch_row_without_registration_id_still_unresolvable():
    """AC-R5: watch present without registration_id still refuses."""
    with (
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={"7188": {"thread_id": "7188"}},
        ),
        patch(
            "claude_bundles.request_admission_identity.resolve_n0_resume_identity",
            return_value=(None, None),
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
        patch(
            "claude_bundles.request_admission_resume._resolve_bus_cse_registration",
            return_value=None,
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
        ],
        "seated_rows": [],
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
        ],
        "seated_rows": [],
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
                    "claude_bundles.request_admission_resume._resolve_bus_cse_registration",
                    return_value=None,
                ),
                patch(
                    "claude_bundles.request_admission_identity.load_active_work_snap_result",
                    return_value=({"rows": [], "seated_rows": []}, False),
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
    assert outcomes == ["reject", "admit", "reject"]
    watch_flags = [c.kwargs["identity"].watch_present for c in emit_mock.call_args_list]
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
            "claude_bundles.request_admission_resume._resolve_bus_cse_registration",
            return_value=None,
        ),
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
            "claude_bundles.request_admission_resume._resolve_bus_cse_registration",
            return_value=None,
        ),
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


def test_census_counts_store_row_and_seated_row_on_same_parent_thread():
    """AC2: one execution-store CSE plus one registry-seated CSE on the same
    parent_thread must both appear in the admission match set.

    Fails on rows-only ``_single_seat_matches`` (today binds N=1
    ``single_seat_active_work`` to the hop row). Passes once census reads
    ``identity_rows``.
    """
    snap = {
        "rows": [
            {
                "execution_id": "hop-exec",
                "registration_id": "fe05-hop",
                "parent_thread": "7188",
                "purpose": "operator-proxy",
                "status": "running",
            }
        ],
        "seated_rows": [
            {
                "execution_id": "__none:seated_no_stream__",
                "registration_id": "cowork-seated",
                "parent_thread": "7188",
                "purpose": "operator-proxy",
                "status": "running",
                "source": "cse-session-registry",
            }
        ],
    }
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
            active_work_snap=snap,
        )
    assert identity.source == "unresolvable"
    assert identity.unresolvable_reason == "ambiguous_matches"
    assert identity.census_n == 2
    assert identity.match_registration_ids == ("fe05-hop", "cowork-seated")


def test_gate_refuses_ambiguous_census_with_structured_reason():
    """AC3: N=2 refuses at enqueue; caller can read reason + match ids."""
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
            active_work_snap=snap,
        )
    assert refusal is not None
    assert refusal["code"] == "seat.identity_unresolvable"
    assert refusal["retryable"] is False
    assert refusal["source"] == "rpc"
    assert "N=2" in refusal["message"]
    assert "reason=ambiguous_matches" in refusal["message"]
    assert "queueing behind" in refusal["message"]
    data = refusal["data"]
    assert data["reason"] == "ambiguous_matches"
    assert data["census_n"] == 2
    assert data["match_registration_ids"] == ["5420b367-a", "5420b367-b"]


def test_origin_cse_does_not_pick_one_when_census_n_ge_2():
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
    with (
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={},
        ),
        patch(
            "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
            return_value="5420b367-a",
        ),
    ):
        identity = resolve_request_admission_identity(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap=snap,
        )
    assert identity.source == "unresolvable"
    assert identity.unresolvable_reason == "ambiguous_matches"
    assert identity.registration_id is None


def test_gate_refuses_zero_matches():
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
            "claude_bundles.request_admission_resume._resolve_bus_cse_registration",
            return_value=None,
        ),
    ):
        refusal = gate_request_admission(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap=snap,
        )
    assert refusal is not None
    assert refusal["code"] == "seat.identity_unresolvable"
    assert refusal["data"]["reason"] == "zero_matches"
    assert refusal["data"]["census_n"] == 0


def test_caller_supplied_admits_when_census_n_ge_2():
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
        refusal = gate_request_admission(
            thread_id="7188",
            caller_registration_id="5420b367-a",
            active_work_snap=snap,
        )
    assert refusal is None


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
            return_value=({"seated_rows": []}, True),
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
