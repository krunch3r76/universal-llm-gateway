"""AC-3: evaluate_watch in-flight inhibit is lane-keyed, not ledger-claimed."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto.hop_cadence_inflight import (
    IN_FLIGHT_COMMISSION_REASON,
    lane_in_flight_commission,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    evaluate_watch,
    save_watches,
)

pytestmark = pytest.mark.offline

_NOW = 1_700_000_000.0
_THREAD = "7232"


def _due_row() -> dict:
    return {
        "thread_id": _THREAD,
        "seated_at": _NOW - 5000.0,
        "from_agent": "web-anthropic",
    }


def _enqueue_claimed(queue, *, thread_id: str = _THREAD):
    job = queue.enqueue(
        thread_id=thread_id,
        turn_number=41,
        subject="TYPE: DIRECTIVE",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor-auto",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    claimed = queue.claim_next()
    assert claimed is not None
    assert claimed.job_id == job.job_id
    return claimed


def test_lane_in_flight_commission_true_when_job_running() -> None:
    """Discriminator +: a job actually running inhibits."""
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    job = _enqueue_claimed(q)
    assert job.status == "claimed"
    assert job.nested_sdk_finished is False
    assert lane_in_flight_commission(_THREAD, queue=q, live_run_fn=lambda _t: None)


def test_lane_in_flight_commission_false_after_closeout_while_claimed() -> None:
    """Fire 5 class: CLOSEOUT on lane, ledger still claimed, must not inhibit.

    t41 CLOSEOUT / t42 residual read claimed / t44 terminalize — naive
    ``status==claimed`` would inhibit; ``nested_sdk_finished`` means the
    lane already has the terminal and the residual claimed read is stale.
    """
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    job = _enqueue_claimed(q)
    q.mark_nested_sdk_finished(job.job_id)
    still = q.get(job.job_id)
    assert still is not None
    assert still.status == "claimed"
    assert still.nested_sdk_finished is True
    assert q.claimed_for_thread(_THREAD) is None
    assert (
        lane_in_flight_commission(_THREAD, queue=q, live_run_fn=lambda _t: None)
        is False
    )


def test_evaluate_watch_inhibits_when_lane_probe_true() -> None:
    decision = evaluate_watch(
        _due_row(),
        now=_NOW,
        threshold=1500.0,
        cool=1800.0,
        in_flight_probe=lambda _tid: True,
    )
    assert decision.action == "skip"
    assert decision.reason == IN_FLIGHT_COMMISSION_REASON


def test_evaluate_watch_fires_when_closeout_claimed_residual() -> None:
    """Ledger-claimed residual must not skip once the lane probe is false."""
    decision = evaluate_watch(
        _due_row(),
        now=_NOW,
        threshold=1500.0,
        cool=1800.0,
        in_flight_probe=lambda _tid: False,
    )
    assert decision.action == "fire"
    assert decision.reason == "age_threshold_met"


@pytest.mark.asyncio
async def test_scan_and_fire_skips_running_commission(tmp_path: Path) -> None:
    """Production wire: scan_and_fire injects the lane probe."""
    from services.git_integration_worker.cursor_auto.hop_cadence import scan_and_fire
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    isolated = tmp_path / "watches.json"
    save_watches({_THREAD: _due_row()}, isolated)
    q = queue_mod.reset_queue_for_tests(durable=False)
    _enqueue_claimed(q)
    with (
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch.registry_started_at",
            return_value=None,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch.assess_standing_handoff",
            return_value=None,
        ),
    ):
        outcomes = await scan_and_fire(queue=q, path=isolated, now=_NOW)
    assert outcomes
    assert outcomes[0]["action"] == "skip"
    assert outcomes[0]["reason"] == IN_FLIGHT_COMMISSION_REASON
