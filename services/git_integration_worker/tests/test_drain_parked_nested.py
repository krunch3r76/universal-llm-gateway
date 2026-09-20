"""Parked nested SDK must yield drain occupancy; live nested SDK must not."""

from __future__ import annotations

from services.git_integration_worker.cursor_auto.drain_parked_nested import (
    waiting_park_resume_for_intent,
)
from services.git_integration_worker.cursor_sdk_park_ledger import (
    PARK_KIND_DISCARD,
    PARK_KIND_RESTART,
    ParkRow,
)


def _park(**overrides: object) -> ParkRow:
    row = {
        "dispatch_id": "auto-x",
        "thread_id": "11667",
        "execution_id": None,
        "caller_agent": None,
        "resolved_model": "composer-2.5",
        "status": "cancelled",
        "terminal_status": "cancelled",
        "sdk_agent_id": None,
        "state_root": None,
        "source_ref": None,
        "work_key": None,
        "contract": None,
        "packet_path": None,
        "park_kind": PARK_KIND_RESTART,
        "park_intent_id": "i-1",
        "parked_at": "2026-09-20T05:16:09+00:00",
        "park_resumed_by": None,
        "park_expires_at": None,
        "record_json": "{}",
    }
    row.update(overrides)
    return ParkRow(**row)  # type: ignore[arg-type]


def test_no_intent_never_waits() -> None:
    assert (
        waiting_park_resume_for_intent(
            job_id="j",
            intent_id=None,
            relay_state={"dispatch_id": "auto-x"},
            park_row=_park(),
        )
        is False
    )


def test_unbound_job_does_not_wait() -> None:
    assert (
        waiting_park_resume_for_intent(
            job_id="j",
            intent_id="i-1",
            relay_state={"dispatch_id": None},
            park_row=_park(),
        )
        is False
    )


def test_matching_open_park_waits() -> None:
    assert (
        waiting_park_resume_for_intent(
            job_id="j",
            intent_id="i-1",
            relay_state={"dispatch_id": "auto-x"},
            park_row=_park(),
        )
        is True
    )


def test_other_intent_still_waits() -> None:
    """recycle_giw can replace the drain intent without restamping the park row."""
    assert (
        waiting_park_resume_for_intent(
            job_id="j",
            intent_id="i-other",
            relay_state={"dispatch_id": "auto-x"},
            park_row=_park(),
        )
        is True
    )


def test_discard_kind_does_not_wait() -> None:
    assert (
        waiting_park_resume_for_intent(
            job_id="j",
            intent_id="i-1",
            relay_state={"dispatch_id": "auto-x"},
            park_row=_park(park_kind=PARK_KIND_DISCARD),
        )
        is False
    )


def test_already_resumed_does_not_wait() -> None:
    assert (
        waiting_park_resume_for_intent(
            job_id="j",
            intent_id="i-1",
            relay_state={"dispatch_id": "auto-x"},
            park_row=_park(park_resumed_by="auto-x-r1"),
        )
        is False
    )
