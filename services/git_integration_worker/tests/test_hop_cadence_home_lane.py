"""Home-lane hop-watch enroll: work-thread Auto jobs alias to the operator lane."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto.hop_cadence_home_lane import (
    home_lane_from_mailbox,
    watch_thread_for_job,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    evaluate_watch,
    load_watches,
    observe_lane_from_enqueue,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob

pytestmark = pytest.mark.offline


def _job(
    *,
    thread_id: str,
    from_agent: str,
    cse_registration_id: str | None = None,
) -> AutoJob:
    return AutoJob(
        job_id="job-home-lane",
        thread_id=thread_id,
        turn_number=1,
        subject="TYPE: DIRECTIVE",
        body="TYPE: DIRECTIVE\n",
        from_agent=from_agent,
        to_agent="cursor-auto",
        desired_model="auto",
        desired_effort="medium",
        contract="investigate",
        cse_registration_id=cse_registration_id,
    )


@pytest.mark.parametrize(
    ("mailbox", "expected"),
    [
        ("cdp-operator-6655-day5i", "6655"),
        ("cdp-operator-6655", "6655"),
        ("cdp_operator_6655_day5j", "6655"),
        ("web-anthropic", None),
        ("cursor", None),
        ("", None),
    ],
)
def test_home_lane_from_mailbox(mailbox: str, expected: str | None) -> None:
    assert home_lane_from_mailbox(mailbox) == expected


def test_watch_thread_for_job_aliases_operator_mailbox() -> None:
    job = _job(thread_id="7170", from_agent="cdp-operator-6655-day5i")
    assert watch_thread_for_job(job) == "6655"


def test_watch_thread_for_job_web_keeps_thread_id() -> None:
    job = _job(thread_id="7059", from_agent="web-anthropic")
    assert watch_thread_for_job(job) == "7059"


def test_observe_work_thread_enrolls_home_lane_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated = tmp_path / "hop_cadence_watches.json"
    monkeypatch.setenv("CURSOR_AUTO_HOP_WATCHES_PATH", str(isolated))
    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_watch.registry_started_at",
        return_value=None,
    ):
        row = observe_lane_from_enqueue(
            _job(thread_id="7170", from_agent="cdp-operator-6655-day5i"),
            now=1_000.0,
        )
    assert row is not None
    assert row["thread_id"] == "6655"
    assert row["seated_at"] == 1_000.0
    watches = load_watches(isolated)
    assert "6655" in watches
    assert "7170" not in watches


def test_observe_aliased_refresh_does_not_reset_seated_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated = tmp_path / "hop_cadence_watches.json"
    monkeypatch.setenv("CURSOR_AUTO_HOP_WATCHES_PATH", str(isolated))
    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_watch.registry_started_at",
        return_value=None,
    ):
        observe_lane_from_enqueue(
            _job(
                thread_id="6655",
                from_agent="cdp-operator-6655-day5i",
                cse_registration_id="reg-home",
            ),
            now=1_000.0,
        )
        observe_lane_from_enqueue(
            _job(
                thread_id="7170",
                from_agent="cdp-operator-6655-day5i",
                cse_registration_id="reg-work",
            ),
            now=1_500.0,
        )
    watches = load_watches(isolated)
    assert "7170" not in watches
    home = watches["6655"]
    assert home["seated_at"] == 1_000.0
    assert home["last_seen_at"] == 1_500.0
    assert home["registration_id"] == "reg-home"


def test_observe_web_anthropic_still_enrolls_job_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated = tmp_path / "hop_cadence_watches.json"
    monkeypatch.setenv("CURSOR_AUTO_HOP_WATCHES_PATH", str(isolated))
    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_watch.registry_started_at",
        return_value=None,
    ):
        observe_lane_from_enqueue(
            _job(thread_id="7059", from_agent="web-anthropic"),
            now=2_000.0,
        )
    watches = load_watches(isolated)
    assert "7059" in watches
    assert watches["7059"]["thread_id"] == "7059"


def test_evaluate_watch_skips_misbound_work_thread_row() -> None:
    row = {
        "thread_id": "7170",
        "from_agent": "cdp-operator-6655-day5i",
        "seated_at": 1.0,
        "purpose": "operator-proxy",
    }
    decision = evaluate_watch(row, now=10_000.0, threshold=100.0, cool=1.0)
    assert decision.action == "skip"
    assert decision.reason == "not_home_lane"
    assert decision.thread_id == "7170"


def test_evaluate_watch_home_lane_row_can_still_fire() -> None:
    row = {
        "thread_id": "6655",
        "from_agent": "cdp-operator-6655-day5j",
        "seated_at": 1.0,
        "purpose": "operator-proxy",
    }
    with (
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
            "current", "cortex://notes/system/threads/6655-standing-handoff.md", None, 1.0
        )
        decision = evaluate_watch(row, now=10_000.0, threshold=100.0, cool=1.0)
    assert decision.action == "fire"
    assert decision.reason == "age_threshold_met"
    assert decision.thread_id == "6655"
