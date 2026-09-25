"""Admit-retry park is a harness service, not a page."""

from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
    PARK_REASON_ADMIT_RETRY_CAP,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_stop_service import (
    admit_retry_park_unserviced,
    budget_park_pages,
)


def test_admit_retry_park_owes_one_service_hop() -> None:
    row = {
        "record_json": (
            '{"hop_parked": true, "hop_park_reason": "hop_budget_admit_retry_cap"}'
        )
    }
    assert admit_retry_park_unserviced(row) is True


def test_serviced_admit_retry_park_does_not_fire_again() -> None:
    row = {
        "record_json": (
            '{"hop_parked": true, "hop_park_reason": "hop_budget_admit_retry_cap",'
            ' "hop_park_serviced": true}'
        )
    }
    assert admit_retry_park_unserviced(row) is False


def test_admit_retry_park_does_not_page() -> None:
    assert budget_park_pages(PARK_REASON_ADMIT_RETRY_CAP) is False
    assert budget_park_pages("hop_budget_crash_cap") is True
