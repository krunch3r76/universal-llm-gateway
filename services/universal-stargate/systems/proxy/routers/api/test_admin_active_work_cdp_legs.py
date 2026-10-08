"""AC1: non-operator open CDP legs make active-work busy; mission legs do not."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from systems.frontier_consult.cdp_generate_reconcile import max_open_leg_s
from systems.proxy.routers.api.admin_active_work import cdp_open_leg_split


def _within_horizon_leg(
    execution_id: str,
    *,
    purpose: str,
    max_wall_s: float = 1800.0,
) -> SimpleNamespace:
    admitted = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    return SimpleNamespace(
        execution_id=execution_id,
        purpose=purpose,
        max_wall_s=max_wall_s,
        admitted_at=admitted,
    )


def _past_horizon_leg(
    execution_id: str,
    *,
    purpose: str,
    max_wall_s: float = 1800.0,
) -> SimpleNamespace:
    admitted = (
        datetime.now(UTC) - timedelta(seconds=max_open_leg_s(max_wall_s) + 10)
    ).isoformat()
    return SimpleNamespace(
        execution_id=execution_id,
        purpose=purpose,
        max_wall_s=max_wall_s,
        admitted_at=admitted,
    )


def test_non_operator_leg_counts_and_mission_legs_are_named_only() -> None:
    non_operator, orphans, past, past_count = cdp_open_leg_split(
        [
            SimpleNamespace(execution_id="exec-consult", purpose="consult"),
            SimpleNamespace(execution_id="exec-proxy", purpose="operator-proxy"),
            SimpleNamespace(execution_id="exec-mission", purpose="mission"),
        ]
    )
    assert non_operator == 1
    assert orphans == [
        {"execution_id": "exec-proxy", "purpose": "operator-proxy"},
        {"execution_id": "exec-mission", "purpose": "mission"},
    ]
    assert past == []
    assert past_count == 0
    total = 0 + 0 + non_operator
    assert total > 0


def test_past_horizon_non_operator_does_not_make_busy() -> None:
    leg = _past_horizon_leg("exec-old", purpose="consult")
    non_operator, orphans, past, past_count = cdp_open_leg_split([leg])
    assert non_operator == 0
    assert orphans == []
    assert past_count == 1
    assert past == [
        {
            "execution_id": "exec-old",
            "purpose": "consult",
            "admitted_at": leg.admitted_at,
        }
    ]


def test_within_horizon_non_operator_counts_toward_busy() -> None:
    non_operator, orphans, past, past_count = cdp_open_leg_split(
        [_within_horizon_leg("exec-live", purpose="consult")]
    )
    assert non_operator == 1
    assert orphans == []
    assert past == []
    assert past_count == 0


def test_exact_horizon_non_operator_is_past_only() -> None:
    """Open seconds equal to max_open_leg_s are past-horizon, not busy."""
    max_wall_s = 1800.0
    admitted = (
        datetime.now(UTC) - timedelta(seconds=max_open_leg_s(max_wall_s))
    ).isoformat()
    leg = SimpleNamespace(
        execution_id="exec-exact",
        purpose="consult",
        max_wall_s=max_wall_s,
        admitted_at=admitted,
    )
    non_operator, orphans, past, past_count = cdp_open_leg_split([leg])
    assert non_operator == 0
    assert orphans == []
    assert past_count == 1
    assert past == [
        {
            "execution_id": "exec-exact",
            "purpose": "consult",
            "admitted_at": leg.admitted_at,
        }
    ]


def test_one_second_under_horizon_non_operator_counts() -> None:
    """Open seconds one under max_open_leg_s still count as non-operator."""
    max_wall_s = 1800.0
    admitted = (
        datetime.now(UTC) - timedelta(seconds=max_open_leg_s(max_wall_s) - 1)
    ).isoformat()
    leg = SimpleNamespace(
        execution_id="exec-under",
        purpose="consult",
        max_wall_s=max_wall_s,
        admitted_at=admitted,
    )
    non_operator, orphans, past, past_count = cdp_open_leg_split([leg])
    assert non_operator == 1
    assert orphans == []
    assert past == []
    assert past_count == 0


def test_past_horizon_operator_proxy_absent_from_orphans() -> None:
    leg = _past_horizon_leg("exec-proxy-old", purpose="operator-proxy")
    non_operator, orphans, past, past_count = cdp_open_leg_split([leg])
    assert non_operator == 0
    assert orphans == []
    assert past_count == 1
    assert past[0]["execution_id"] == "exec-proxy-old"
    assert past[0]["purpose"] == "operator-proxy"
