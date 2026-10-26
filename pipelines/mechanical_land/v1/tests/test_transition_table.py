"""Exhaustive §6 transition table parametrization."""

from __future__ import annotations

import pytest
from mechanical_land.policy import PolicyInvalidError, policy_sha256, stamp_policy
from mechanical_land.schema import (
    GateResult,
    GateVerdict,
    LandState,
    LegClosedPayload,
    LegRecord,
    ReviewState,
    ReviewVerdict,
    RunState,
)
from mechanical_land.transition import transition

pytestmark = pytest.mark.offline

HEX = "f" * 40
POLICY = {
    "review": {"route_order": ["bus_request", "check_review"]},
    "retry": {"gate_fix_max": 1, "review_fix_max": 1},
}


def _state(**kwargs: object) -> RunState:
    s = RunState(kind="implement")
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


@pytest.mark.parametrize(
    "state_kwargs,expected",
    [
        pytest.param(
            {"step": "run", "kind": "implement"},
            ("admit", "implement", 1, None, None),
            id="run_birthed_to_implement",
        ),
        pytest.param(
            {
                "step": "implement",
                "last_closeout": LegClosedPayload(
                    dispatch_id="d1",
                    closeout_status="complete",
                    work_outcome="ok",
                    head_sha=HEX,
                    branch="b",
                    branch_point=HEX,
                ),
            },
            ("gate", "gate", 1, None, None),
            id="implement_closed_ok_to_gate",
        ),
        pytest.param(
            {
                "step": "implement",
                "last_closeout": LegClosedPayload(
                    dispatch_id="d1",
                    closeout_status="failed",
                    work_outcome="failed",
                    head_sha=HEX,
                    branch="b",
                    branch_point=HEX,
                ),
            },
            ("block", None, None, None, "leg_failed"),
            id="implement_failed_blocked",
        ),
        pytest.param(
            {
                "step": "gate",
                "gate_results": {
                    1: GateResult(1, GateVerdict.GREEN, HEX),
                },
            },
            ("review", "review", 1, "bus_request", None),
            id="gate_green_to_review_bus_request",
        ),
        pytest.param(
            {
                "step": "gate",
                "gate_fix_used": 0,
                "gate_results": {
                    1: GateResult(1, GateVerdict.RED, HEX, ruff_exit=1),
                },
            },
            ("admit", "fix", 1, None, None),
            id="gate_red_gate_fix_used_0_to_fix",
        ),
        pytest.param(
            {
                "step": "gate",
                "gate_fix_used": 1,
                "gate_results": {
                    2: GateResult(2, GateVerdict.RED, HEX, ruff_exit=1),
                },
            },
            ("block", None, None, None, "gate_red_after_fix"),
            id="gate_red_after_fix_blocked",
        ),
        pytest.param(
            {
                "step": "gate",
                "gate_results": {
                    1: GateResult(1, GateVerdict.INFRA_ERROR, HEX),
                },
            },
            ("block", None, None, None, "gate_infra"),
            id="gate_infra_blocked",
        ),
        pytest.param(
            {
                "step": "review",
                "gate_results": {1: GateResult(1, GateVerdict.GREEN, HEX)},
                "review": ReviewState(
                    tip_sha=HEX,
                    verdict=ReviewVerdict.APPROVE,
                    route="bus_request",
                ),
            },
            ("land", "land", 1, None, None),
            id="review_bus_request_approve_to_land",
        ),
        pytest.param(
            {
                "step": "review",
                "gate_results": {1: GateResult(1, GateVerdict.GREEN, HEX)},
                "review": ReviewState(
                    tip_sha=HEX,
                    verdict=ReviewVerdict.APPROVE,
                    route="check_review",
                ),
            },
            ("land", "land", 1, None, None),
            id="review_check_review_approve_to_land",
        ),
        pytest.param(
            {
                "step": "review",
                "review": ReviewState(
                    verdict=ReviewVerdict.UNBOUND, route="bus_request"
                ),
            },
            ("review", "review", 1, "check_review", None),
            id="bus_request_unbound_fallback_check_review",
        ),
        pytest.param(
            {
                "step": "review",
                "review": ReviewState(
                    verdict=ReviewVerdict.UNBOUND, route="check_review"
                ),
            },
            ("block", None, None, None, "review_unbound"),
            id="review_unbound_blocked",
        ),
        pytest.param(
            {
                "step": "review",
                "review_fix_used": 0,
                "review": ReviewState(
                    verdict=ReviewVerdict.REQUEST_CHANGES,
                    blockers=("B1",),
                ),
            },
            ("admit", "fix", 1, None, None),
            id="request_changes_review_fix_0_to_fix",
        ),
        pytest.param(
            {
                "step": "review",
                "review_fix_used": 1,
                "review": ReviewState(
                    verdict=ReviewVerdict.REQUEST_CHANGES,
                    blockers=("B1",),
                ),
            },
            ("block", None, None, None, "review_request_changes"),
            id="request_changes_review_fix_1_blocked",
        ),
        pytest.param(
            {
                "step": "review",
                "gate_results": {1: GateResult(1, GateVerdict.GREEN, HEX)},
                "review": ReviewState(tip_sha="0" * 40, verdict=ReviewVerdict.APPROVE),
            },
            ("block", None, None, None, "tip_drift"),
            id="tip_drift_blocked",
        ),
        pytest.param(
            {
                "step": "land",
                "land": LandState(landed=True, tip_sha=HEX),
            },
            ("restart", "restart", 1, None, None),
            id="land_landed_to_restart",
        ),
        pytest.param(
            {"step": "close", "status": "closed"},
            ("noop", None, None, None, None),
            id="close_ok_noop",
        ),
        pytest.param(
            {"pending_hold": True, "status": "blocked_hold", "step": "gate"},
            ("wait", None, None, None, None),
            id="ruling_hold_overlay",
        ),
    ],
)
def test_transition_row(state_kwargs, expected) -> None:
    cmd, step_id, attempt, route, reason_code = expected
    st = _state(**state_kwargs)
    tr = transition(POLICY, st)
    assert tr.command == cmd
    if step_id is not None:
        assert tr.step_id == step_id
    if attempt is not None:
        assert tr.attempt == attempt
    if route is not None:
        assert tr.review_route == route
    if reason_code:
        assert tr.reason_code == reason_code


def test_q5_cycle_chain() -> None:
    st = _state(
        step="review",
        review_fix_used=0,
        review=ReviewState(
            verdict=ReviewVerdict.REQUEST_CHANGES,
            blockers=("B1",),
        ),
    )
    tr1 = transition(POLICY, st)
    assert tr1.command == "admit" and tr1.reason == "review_request_changes"
    st.review_fix_used = 1
    st.step = "review"
    st.review = ReviewState(
        verdict=ReviewVerdict.REQUEST_CHANGES,
        blockers=("B2",),
    )
    tr2 = transition(POLICY, st)
    assert tr2.blocked and tr2.reason_code == "review_request_changes"


def test_policy_sha256_stable_under_key_reordering() -> None:
    a, sa = stamp_policy(
        explicit={"gate": {"ruff": True, "pytest_scope": "changed_dirs"}}
    )
    b, sb = stamp_policy(
        explicit={"gate": {"pytest_scope": "changed_dirs", "ruff": True}}
    )
    assert sa == sb
    assert sa == policy_sha256(a)


def test_stamp_refuses_forbidden_restart_flags() -> None:
    with pytest.raises(PolicyInvalidError) as exc:
        stamp_policy(explicit={"restart": {"wait_for_boundary": True}})
    assert exc.value.code == "policy_invalid"
    with pytest.raises(PolicyInvalidError):
        stamp_policy(explicit={"restart": {"force_giw": True}})
    with pytest.raises(PolicyInvalidError):
        stamp_policy(explicit={"restart": {"propagate": True}})


def test_stamp_refuses_invalid_route_order() -> None:
    with pytest.raises(PolicyInvalidError):
        stamp_policy(explicit={"review": {"route_order": ["not_a_route"]}})


def test_result_json_section9_fields() -> None:
    from mechanical_land.fold import fold

    leg = LegRecord(
        step_id="implement",
        attempt=1,
        flow="main",
        dispatch_id="d1",
        closeout_status="complete",
        head_sha=HEX,
    )
    st = RunState(
        run_id="ml-t",
        kind="implement",
        status="closed",
        legs_journal_order=[leg],
        land=LandState(before_sha=HEX, after_sha=HEX, landed=True),
        restart=RunState().restart,
    )
    st.review_history.append(
        __import__(
            "mechanical_land.schema", fromlist=["ReviewAttemptRecord"]
        ).ReviewAttemptRecord(attempt=1, verdict="APPROVE", tip_sha=HEX)
    )
    rj = st.result_json()
    assert isinstance(rj["review"], list)
    assert rj["land"]["pushed"] is False
    assert rj["restart"]["wait_for_boundary"] is False
    assert rj["legs"][0]["step"] == "implement"
    assert fold([]).result_json()["review"] is None
