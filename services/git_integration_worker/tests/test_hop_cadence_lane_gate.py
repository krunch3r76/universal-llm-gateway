"""Hop-cadence nested work-lane gate — 9540 excluded, 9534 eligible."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto.hop_cadence_lane_gate import (
    SKIP_NESTED_WORK_LANE,
    hop_cadence_lane_skip_reason,
    nested_sub_mission_work_lane,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    evaluate_watch,
    observe_lane_from_enqueue,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob

pytestmark = pytest.mark.offline


def _lane(*, state: str = "associated", lane_role: str | None, parent: str | None):
    return {
        "state": state,
        "lane_role": lane_role,
        "parent_thread": parent,
    }


def _job(*, thread_id: str, from_agent: str = "web-anthropic") -> AutoJob:
    return AutoJob(
        job_id=f"job-{thread_id}",
        thread_id=thread_id,
        turn_number=1,
        subject="TYPE: DIRECTIVE",
        body="TYPE: DIRECTIVE\n",
        from_agent=from_agent,
        to_agent="cursor-auto",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )


@pytest.mark.parametrize(
    ("thread_id", "self_lane", "parent_lane", "expected_blocked"),
    [
        (
            "9540",
            _lane(lane_role="sub_mission", parent="9534"),
            _lane(lane_role="sub_mission", parent="9530"),
            True,
        ),
        (
            "9534",
            _lane(lane_role="sub_mission", parent="9530"),
            _lane(lane_role=None, parent=None),
            False,
        ),
        (
            "9530",
            _lane(lane_role=None, parent=None),
            None,
            False,
        ),
    ],
)
def test_nested_sub_mission_work_lane_probe(
    thread_id: str,
    self_lane: dict,
    parent_lane: dict | None,
    expected_blocked: bool,
) -> None:
    parent_id = str(self_lane.get("parent_thread") or "")

    def _fake_get_current_lane(*, thread_id: str):
        if thread_id == tid:
            return {**self_lane, "thread_id": thread_id}
        if parent_id and thread_id == parent_id and parent_lane is not None:
            return {**parent_lane, "thread_id": thread_id}
        raise LookupError(thread_id)

    tid = thread_id
    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_lane_gate.get_current_lane",
        side_effect=_fake_get_current_lane,
    ):
        blocked, reason = nested_sub_mission_work_lane(thread_id)
    assert blocked is expected_blocked
    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_lane_gate.get_current_lane",
        side_effect=_fake_get_current_lane,
    ):
        skip = hop_cadence_lane_skip_reason(thread_id)
    if expected_blocked:
        assert reason == SKIP_NESTED_WORK_LANE
        assert skip == SKIP_NESTED_WORK_LANE
    else:
        assert reason is None
        assert skip is None


def test_observe_skips_nested_work_lane_9540(tmp_path: Path) -> None:
    isolated = tmp_path / "hop_cadence_watches.json"
    with (
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch.hop_cadence_lane_skip_reason",
            return_value=SKIP_NESTED_WORK_LANE,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch.registry_started_at",
            return_value=None,
        ),
    ):
        row = observe_lane_from_enqueue(_job(thread_id="9540"), path=isolated)
    assert row is None
    assert not isolated.exists() or isolated.read_text().strip() in ("", "{}")


def test_evaluate_watch_9540_skips_nested_work_lane() -> None:
    row = {
        "thread_id": "9540",
        "from_agent": "web-anthropic",
        "seated_at": 1.0,
        "purpose": "operator-proxy",
    }
    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_watch.hop_cadence_lane_skip_reason",
        return_value=SKIP_NESTED_WORK_LANE,
    ):
        decision = evaluate_watch(row, now=10_000.0, threshold=100.0, cool=1.0)
    assert decision.action == "skip"
    assert decision.reason == SKIP_NESTED_WORK_LANE
    assert decision.thread_id == "9540"


def test_evaluate_watch_9534_operator_lane_can_fire() -> None:
    row = {
        "thread_id": "9534",
        "from_agent": "web-anthropic",
        "seated_at": 1.0,
        "purpose": "operator-proxy",
    }
    with (
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch.hop_cadence_lane_skip_reason",
            return_value=None,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.breaker_blocks_hop",
            return_value=False,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch.registry_started_at",
            return_value=None,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch.assess_standing_handoff",
        ) as handoff,
    ):
        from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
            StandingHandoffFreshness,
        )

        handoff.return_value = StandingHandoffFreshness(
            "current", "cortex://notes/system/threads/9534-standing-handoff.md", None, 1.0
        )
        decision = evaluate_watch(row, now=10_000.0, threshold=100.0, cool=1.0)
    assert decision.action == "fire"
    assert decision.reason == "age_threshold_met"
    assert decision.thread_id == "9534"
