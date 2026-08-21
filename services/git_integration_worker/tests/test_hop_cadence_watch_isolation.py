"""Guard: hop-cadence tests must not write the production watch ledger."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto.hop_cadence import CapacityGateResult
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    load_watches,
    mark_hop_failed,
    observe_lane_from_enqueue,
    watches_path,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob


def _production_ledger_path() -> Path:
    return Path.home() / ".gateway" / "cdp-registry" / "hop_cadence_watches.json"


def _production_mtime() -> float | None:
    path = _production_ledger_path()
    if not path.is_file():
        return None
    return path.stat().st_mtime


def _web_job(*, thread_id: str = "7059-isolation") -> AutoJob:
    return AutoJob(
        job_id="job-7059",
        thread_id=thread_id,
        turn_number=1,
        subject="TYPE: DIRECTIVE",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor-auto",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )


def test_watches_path_honors_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated = tmp_path / "isolated_watches.json"
    monkeypatch.setenv("CURSOR_AUTO_HOP_WATCHES_PATH", str(isolated))
    assert watches_path() == isolated


def test_watches_path_defaults_to_home_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURSOR_AUTO_HOP_WATCHES_PATH", raising=False)
    assert watches_path() == _production_ledger_path()


def test_observe_and_mark_hop_failed_do_not_touch_production_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under env redirect, observe + mark_hop_failed write only the test ledger."""
    isolated = tmp_path / "hop_cadence_watches.json"
    monkeypatch.setenv("CURSOR_AUTO_HOP_WATCHES_PATH", str(isolated))
    before_mtime = _production_mtime()

    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_watch.registry_started_at",
        return_value=None,
    ):
        observe_lane_from_enqueue(_web_job(thread_id="7059-observe"))

    mark_hop_failed("7059-observe", reason="missing_execution_id", now=1_000_000.0)

    assert isolated.is_file()
    rows = load_watches(isolated)
    assert "7059-observe" in rows
    assert rows["7059-observe"]["last_hop_failure_reason"] == "missing_execution_id"

    after_mtime = _production_mtime()
    assert after_mtime == before_mtime


def test_observe_captures_mission_once_from_vision_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated = tmp_path / "hop_cadence_watches.json"
    monkeypatch.setenv("CURSOR_AUTO_HOP_WATCHES_PATH", str(isolated))
    job = _web_job(thread_id="7059-mission")
    job.body = "TYPE: DIRECTIVE\nvision: Recover the operator-proxy continuity arc.\n"
    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_watch.registry_started_at",
        return_value=None,
    ):
        observe_lane_from_enqueue(job)
    rows = load_watches(isolated)
    assert (
        rows["7059-mission"]["mission"] == "Recover the operator-proxy continuity arc."
    )


def test_observe_does_not_overwrite_a_captured_mission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First captured mission is pinned — a later, less descriptive turn must not win."""
    isolated = tmp_path / "hop_cadence_watches.json"
    monkeypatch.setenv("CURSOR_AUTO_HOP_WATCHES_PATH", str(isolated))
    first = _web_job(thread_id="7059-pin")
    first.body = "vision: First mission statement.\n"
    second = _web_job(thread_id="7059-pin")
    second.body = "vision: Second mission statement must not win.\n"
    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_watch.registry_started_at",
        return_value=None,
    ):
        observe_lane_from_enqueue(first, now=1_000.0)
        observe_lane_from_enqueue(second, now=1_500.0)
    rows = load_watches(isolated)
    assert rows["7059-pin"]["mission"] == "First mission statement."


@pytest.mark.asyncio
async def test_fire_hop_for_decision_forwards_path_to_mark_helpers(
    tmp_path: Path,
) -> None:
    """fire_hop_for_decision path= reaches mark_hop_failed without touching production."""
    from services.git_integration_worker.cursor_auto.hop_cadence import (
        fire_hop_for_decision,
    )
    from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
        HopDecision,
        save_watches,
    )
    from services.git_integration_worker.cursor_auto.queue import AutoJobQueue

    isolated = tmp_path / "hop_cadence_watches.json"
    now = 2_000_000.0
    row = {
        "thread_id": "7059-fire",
        "seated_at": now - 2000.0,
        "from_agent": "web-anthropic",
    }
    save_watches({"7059-fire": row}, isolated)
    before_mtime = _production_mtime()

    queue = AutoJobQueue(durable=False)

    async def _hop_no_id(job, *, queue, incumbent=None):
        return {"ok": True, "reason": "continuity_hop_cdp_commissioned"}

    with (
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence."
            "run_continuity_hop_concurrent",
            new=_hop_no_id,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence."
            "capacity_blocks_hop",
            lambda **_: CapacityGateResult.fail_open(),
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence."
            "assess_standing_handoff",
            lambda tid: __import__(
                "services.git_integration_worker.cursor_auto.hop_cadence_watch",
                fromlist=["StandingHandoffFreshness"],
            ).StandingHandoffFreshness("current", f"cortex://x/{tid}.md", None, 1.0),
        ),
    ):
        await fire_hop_for_decision(
            HopDecision(
                thread_id="7059-fire",
                action="fire",
                reason="age_threshold_met",
                age_s=2000.0,
                threshold_s=1500.0,
                signal="watch_seated_at",
            ),
            queue=queue,
            row=row,
            path=isolated,
        )

    rows = json.loads(isolated.read_text(encoding="utf-8"))
    assert rows["7059-fire"]["last_hop_failure_reason"] == "missing_execution_id"
    after_mtime = _production_mtime()
    assert after_mtime == before_mtime
