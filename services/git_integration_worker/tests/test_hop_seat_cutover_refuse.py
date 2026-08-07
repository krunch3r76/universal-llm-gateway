"""Hop seat cutover refuse-at-request — cadence repeat + predecessor request gate."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_bundles.hop_seat_cutover import (
    effective_seated_at_after_hop,
    refuse_cadence_hop_for_live_seat,
    resolve_request_refusal,
)
from services.git_integration_worker.cursor_auto.hop_cadence import fire_hop_for_decision
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    HopDecision,
    evaluate_watch,
)


def _snap(*, registration_id: str, execution_id: str = "exec-successor") -> dict:
    return {
        "rows": [
            {
                "execution_id": execution_id,
                "registration_id": registration_id,
                "status": "running",
                "purpose": "operator-proxy",
            }
        ]
    }


def test_effective_seated_at_prefers_post_hop_reset_over_stale_registry():
    """Would fail before fix: registry started_at kept age above threshold after hop."""
    now = 2_000_000.0
    row = {
        "thread_id": "6885",
        "registration_id": "reg-live",
        "seated_at": now - 100.0,
        "last_hop_at": now - 2000.0,
    }

    def _registry_started(_reg: str | None) -> float | None:
        return now - 5_000.0

    seated = effective_seated_at_after_hop(row, registry_started_at=_registry_started)
    assert seated == now - 100.0
    decision = evaluate_watch(row, now=now, threshold=1500.0, cool=1800.0)
    assert decision.action == "skip"
    assert decision.reason == "below_threshold"


def test_refuse_cadence_hop_while_same_registration_still_running():
    row = {
        "registration_id": "reg-incumbent",
        "last_hop_at": time.time() - 60.0,
    }
    refuse, reason, evidence = refuse_cadence_hop_for_live_seat(row, _snap(registration_id="reg-incumbent"))
    assert refuse is True
    assert reason == "seat_live_refuse_at_request"
    assert evidence["registration_id"] == "reg-incumbent"


def test_first_cadence_hop_allowed_while_seat_live():
    row = {"registration_id": "reg-incumbent", "last_hop_at": None}
    refuse, reason, _ = refuse_cadence_hop_for_live_seat(row, _snap(registration_id="reg-incumbent"))
    assert refuse is False
    assert reason is None


def test_resolve_request_refusal_after_successor_confirm():
    row = {
        "superseded_registration_id": "reg-old",
        "successor_execution_id": "exec-new",
    }
    refusal = resolve_request_refusal(
        thread_id="6885",
        cse_registration_id="reg-old",
        snap=_snap(registration_id="reg-new", execution_id="exec-new"),
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
            snap=_snap(registration_id="reg-new", execution_id="exec-new"),
        )
    assert refusal is not None
    assert refusal["reason"] == "superseded_predecessor_refuse_at_request"
    assert refusal["successor_execution_id"] == "exec-new"


@pytest.mark.asyncio
async def test_fire_hop_refuses_repeat_while_registration_streams():
    """Fails before change: cadence re-commissions against a live incumbent seat."""
    decision = HopDecision(
        "6885",
        "fire",
        "age_threshold_met",
        age_s=2000.0,
        threshold_s=1500.0,
    )
    row = {
        "registration_id": "reg-live",
        "last_hop_at": time.time() - 100.0,
        "from_agent": "web-anthropic",
    }
    queue = MagicMock()
    job = MagicMock(job_id="job-hop")
    queue.enqueue.return_value = job
    snap = _snap(registration_id="reg-live")

    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence.read_cdp_lane_snapshot",
        return_value=snap,
    ), patch(
        "services.git_integration_worker.cursor_auto.hop_cadence.run_continuity_hop_concurrent",
        new_callable=AsyncMock,
        return_value={"ok": True, "execution_id": "exec-new"},
    ):
        outcome = await fire_hop_for_decision(
            decision,
            queue=queue,
            row=row,
            snapshot_reader=lambda: snap,
        )

    assert outcome["ok"] is False
    assert outcome["reason"] == "seat_live_refuse_at_request"
    queue.enqueue.assert_not_called()
