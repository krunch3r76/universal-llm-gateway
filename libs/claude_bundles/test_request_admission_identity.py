"""Identity-on-gate observation — resolve from watch row, admission unchanged."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from claude_bundles.request_admission_identity import (
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


def test_resolve_from_watch_row_when_caller_omits_registration():
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"7188": {"registration_id": "5420b367-7188"}},
    ):
        identity = resolve_request_admission_identity(
            thread_id="7188",
            caller_registration_id=None,
        )
    assert identity.source == "watch_row"
    assert identity.registration_id == "5420b367-7188"
    assert identity.watch_present is True


def test_unresolvable_on_watch_lane_without_registration():
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"7188": {"thread_id": "7188"}},
    ):
        identity = resolve_request_admission_identity(
            thread_id="7188",
            caller_registration_id=None,
        )
    assert identity.source == "unresolvable"
    assert identity.registration_id is None
    assert identity.watch_present is True


def test_admission_verdict_unchanged_without_caller_registration():
    """Fails if server-resolved identity is wired into the live refusal gate."""
    from tools.agent_bus.request import _resolve_hop_seat_request_refusal

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
    ):
        with patch(
            "cdp_ask.client.CdpAskClient",
        ) as client_cls:
            client_cls.return_value._request.return_value = snap
            refusal = _resolve_hop_seat_request_refusal(
                thread_id="7188",
                cse_registration_id=None,
            )
    assert refusal is None

    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={
            "7188": {
                **row,
                "registration_id": "5420b367-old",
            }
        },
    ):
        observe_identity_on_gate(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap=snap,
        )
    counters = get_identity_counters()
    assert counters.get("identity_source:unresolvable", 0) == 0
    assert counters["identity_source:watch_row"] == 1
    assert counters["counterfactual_would_refuse"] == 1
    assert counters.get("unresolvable_on_watch_lane", 0) == 0


def test_counterfactual_admit_when_identity_unresolvable_on_watch_lane():
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={"7188": {"thread_id": "7188"}},
    ):
        observe_identity_on_gate(
            thread_id="7188",
            caller_registration_id=None,
            active_work_snap={"rows": []},
        )
    counters = get_identity_counters()
    assert counters["unresolvable_on_watch_lane"] == 1
    assert counters["counterfactual_would_admit"] == 1


def test_request_dispatch_observes_before_refusal_gate():
    from tools.agent_bus.request import _request_dispatch

    observed: list[str] = []

    def fake_observe(**kwargs):
        observed.append(kwargs.get("thread_id"))
        return None

    with (
        patch(
            "tools.agent_bus.request._observe_request_admission_identity",
            side_effect=fake_observe,
        ),
        patch(
            "tools.agent_bus.request._resolve_hop_seat_request_refusal",
            return_value={"error": "blocked", "status_code": 422},
        ),
    ):
        result = _request_dispatch(
            thread="7188",
            subject="s",
            body="b",
            from_agent="web-anthropic",
        )
    assert observed == ["7188"]
    assert result.get("status_code") == 422
