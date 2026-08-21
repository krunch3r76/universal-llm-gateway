"""Hop-cadence skip when the operator is PARKED waiting on Auto."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from claude_bundles.cse_session_common import is_parked_waiting_body

from services.git_integration_worker.cursor_auto.hop_cadence_waiting import (
    AUTO_IN_FLIGHT_REASON,
    IDLE_NO_KEEPALIVE_REASON,
    PARKED_WAITING_REASON,
    cadence_skip_reason,
    lane_parked_waiting,
    mark_watch_wait_report,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import save_watches

pytestmark = pytest.mark.offline

_NOW = 1_700_000_000.0
_THREAD = "9538"


def _due_row(**extra: object) -> dict:
    row = {
        "thread_id": _THREAD,
        "seated_at": _NOW - 5000.0,
        "from_agent": "web-anthropic",
    }
    row.update(extra)
    return row


def _job_kwargs(**extra: object) -> dict:
    body = {
        "thread_id": _THREAD,
        "turn_number": 1,
        "subject": "TYPE: DIRECTIVE",
        "body": "TYPE: DIRECTIVE\n",
        "from_agent": "web-anthropic",
        "to_agent": "cursor-auto",
        "desired_model": "auto",
        "desired_effort": "medium",
        "contract": "implement",
    }
    body.update(extra)
    return body


def test_is_parked_waiting_body_shapes() -> None:
    assert is_parked_waiting_body("TYPE: WAITING\nwaiting_on: queue") is True
    assert (
        is_parked_waiting_body(
            "TYPE: PARKED\nwaiting_on: cursor-auto serial queue\n"
        )
        is True
    )
    assert is_parked_waiting_body("TYPE: PARKED\nwake: chat_delivery\n") is False
    assert is_parked_waiting_body("hello") is False


def test_lane_parked_waiting_latest_parked_with_waiting_on() -> None:
    turns = [
        {"turn_number": 1, "body": "hello"},
        {
            "turn_number": 9,
            "body": "TYPE: PARKED\nwaiting_on: cursor-auto serial queue\n",
        },
    ]
    assert lane_parked_waiting(_THREAD, fetch_turns_fn=lambda _tid: turns) is True


def test_lane_parked_waiting_closeout_park_does_not_match() -> None:
    turns = [
        {"turn_number": 9, "body": "TYPE: PARKED\nwake: chat_delivery\n"},
    ]
    assert lane_parked_waiting(_THREAD, fetch_turns_fn=lambda _tid: turns) is False


def test_cadence_skip_reason_stamped_queued_job() -> None:
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    job = q.enqueue(**_job_kwargs())
    row = _due_row(wait_report_job_id=job.job_id)
    assert (
        cadence_skip_reason(
            _THREAD,
            row=row,
            queue=q,
            fetch_turns_fn=lambda _tid: [],
        )
        == PARKED_WAITING_REASON
    )


def test_cadence_skip_reason_queued_auto_same_thread_without_stamp() -> None:
    """Queued Auto on the watch thread inhibits even with no wait-report stamp."""
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    q.enqueue(**_job_kwargs())
    assert (
        cadence_skip_reason(
            _THREAD,
            row=_due_row(),
            queue=q,
            fetch_turns_fn=lambda _tid: [],
        )
        == AUTO_IN_FLIGHT_REASON
    )


def test_cadence_skip_reason_web_anthropic_child_thread() -> None:
    """web-anthropic Auto on a work thread still binds the operator hop watch."""
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    q.enqueue(**_job_kwargs(thread_id="9539"))
    assert (
        cadence_skip_reason(
            _THREAD,
            row=_due_row(),
            queue=q,
            fetch_turns_fn=lambda _tid: [],
        )
        == AUTO_IN_FLIGHT_REASON
    )


def test_cadence_skip_reason_operator_mailbox_work_thread() -> None:
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    q.enqueue(
        **_job_kwargs(
            thread_id="8001",
            from_agent="cdp-operator-9538-day5i",
        )
    )
    row = _due_row(from_agent="cdp-operator-9538-day5j")
    assert (
        cadence_skip_reason(
            _THREAD,
            row=row,
            queue=q,
            fetch_turns_fn=lambda _tid: [],
        )
        == AUTO_IN_FLIGHT_REASON
    )


def test_cadence_skip_reason_cse_registration_match() -> None:
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    q.enqueue(
        **_job_kwargs(
            thread_id="8001",
            from_agent="cursor",
            cse_registration_id="reg-home",
        )
    )
    row = _due_row(registration_id="reg-home")
    assert (
        cadence_skip_reason(
            _THREAD,
            row=row,
            queue=q,
            fetch_turns_fn=lambda _tid: [],
        )
        == AUTO_IN_FLIGHT_REASON
    )


def test_cadence_skip_reason_continuity_hop_does_not_inhibit() -> None:
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    q.enqueue(**_job_kwargs(continuity_hop=True))
    assert (
        cadence_skip_reason(
            _THREAD,
            row=_due_row(),
            queue=q,
            fetch_turns_fn=lambda _tid: [],
        )
        == IDLE_NO_KEEPALIVE_REASON
    )


def test_cadence_skip_reason_nested_sdk_finished_does_not_inhibit() -> None:
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    job = q.enqueue(**_job_kwargs())
    q.claim_next()
    q.mark_nested_sdk_finished(job.job_id)
    assert (
        cadence_skip_reason(
            _THREAD,
            row=_due_row(),
            queue=q,
            fetch_turns_fn=lambda _tid: [],
        )
        == IDLE_NO_KEEPALIVE_REASON
    )


def test_cadence_skip_reason_unrelated_lane_does_not_inhibit() -> None:
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    q.enqueue(
        **_job_kwargs(
            thread_id="9999",
            from_agent="cdp-operator-1111-day5i",
        )
    )
    assert (
        cadence_skip_reason(
            _THREAD,
            row=_due_row(),
            queue=q,
            fetch_turns_fn=lambda _tid: [],
        )
        == IDLE_NO_KEEPALIVE_REASON
    )


def test_cadence_skip_reason_clears_when_job_done() -> None:
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    job = q.enqueue(**_job_kwargs())
    q.mark_done(job.job_id)
    row = _due_row(wait_report_job_id=job.job_id)
    assert (
        cadence_skip_reason(
            _THREAD,
            row=row,
            queue=q,
            fetch_turns_fn=lambda _tid: [],
        )
        == IDLE_NO_KEEPALIVE_REASON
    )


def test_mark_watch_wait_report_persists(tmp_path: Path) -> None:
    isolated = tmp_path / "watches.json"
    save_watches({_THREAD: _due_row()}, isolated)
    mark_watch_wait_report(_THREAD, "job-wait", path=isolated)
    from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
        load_watches,
    )

    rows = load_watches(isolated)
    assert rows[_THREAD]["wait_report_job_id"] == "job-wait"


@pytest.mark.asyncio
async def test_scan_and_fire_skips_auto_in_flight(tmp_path: Path) -> None:
    from services.git_integration_worker.cursor_auto import queue as queue_mod
    from services.git_integration_worker.cursor_auto.hop_cadence import scan_and_fire

    isolated = tmp_path / "watches.json"
    q = queue_mod.reset_queue_for_tests(durable=False)
    q.enqueue(**_job_kwargs(thread_id="9539"))
    save_watches({_THREAD: _due_row()}, isolated)
    with (
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch.registry_started_at",
            return_value=None,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch.assess_standing_handoff",
            return_value=None,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_standdown._fetch_thread_turns_sync",
            return_value=[],
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence.fire_hop_for_decision",
        ) as fire,
    ):
        outcomes = await scan_and_fire(queue=q, path=isolated, now=_NOW)
    fire.assert_not_called()
    assert outcomes
    assert outcomes[0]["action"] == "skip"
    assert outcomes[0]["reason"] == AUTO_IN_FLIGHT_REASON


@pytest.mark.asyncio
async def test_scan_and_fire_skips_parked_waiting(tmp_path: Path) -> None:
    from services.git_integration_worker.cursor_auto import queue as queue_mod
    from services.git_integration_worker.cursor_auto.hop_cadence import scan_and_fire

    isolated = tmp_path / "watches.json"
    q = queue_mod.reset_queue_for_tests(durable=False)
    job = q.enqueue(**_job_kwargs())
    save_watches({_THREAD: _due_row(wait_report_job_id=job.job_id)}, isolated)
    with (
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch.registry_started_at",
            return_value=None,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch.assess_standing_handoff",
            return_value=None,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_standdown._fetch_thread_turns_sync",
            return_value=[],
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence.fire_hop_for_decision",
        ) as fire,
    ):
        outcomes = await scan_and_fire(queue=q, path=isolated, now=_NOW)
    fire.assert_not_called()
    assert outcomes
    assert outcomes[0]["action"] == "skip"
    assert outcomes[0]["reason"] == PARKED_WAITING_REASON


@pytest.mark.asyncio
async def test_scan_and_fire_skips_idle_lane(tmp_path: Path) -> None:
    from services.git_integration_worker.cursor_auto import queue as queue_mod
    from services.git_integration_worker.cursor_auto.hop_cadence import scan_and_fire

    isolated = tmp_path / "watches.json"
    q = queue_mod.reset_queue_for_tests(durable=False)
    save_watches({_THREAD: _due_row()}, isolated)
    with (
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch.registry_started_at",
            return_value=None,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch.assess_standing_handoff",
            return_value=None,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_standdown._fetch_thread_turns_sync",
            return_value=[],
        ),
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence.fire_hop_for_decision",
        ) as fire,
    ):
        outcomes = await scan_and_fire(queue=q, path=isolated, now=_NOW)
    fire.assert_not_called()
    assert outcomes
    assert outcomes[0]["action"] == "skip"
    assert outcomes[0]["reason"] == IDLE_NO_KEEPALIVE_REASON
