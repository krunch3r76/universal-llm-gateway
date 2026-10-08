"""AC1: non-operator open CDP legs make active-work busy; mission legs do not."""

from __future__ import annotations

from types import SimpleNamespace

from systems.proxy.routers.api.admin_active_work import cdp_open_leg_split


def test_non_operator_leg_counts_and_mission_legs_are_named_only() -> None:
    non_operator, orphans = cdp_open_leg_split(
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
    total = 0 + 0 + non_operator
    assert total > 0
