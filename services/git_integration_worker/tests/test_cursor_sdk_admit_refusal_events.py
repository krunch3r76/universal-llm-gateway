"""Unit tests for the frontier.sdk.admit.work_key_* refusal events.

These events are how a refused admission becomes visible after the fact: the
HTTP 409 reaches only the caller, so if the payload is wrong or a field is
silently dropped, an operator reconstructing why a dispatch never ran has
nothing to read. Master shipped the whole family without coverage.
"""

from __future__ import annotations

from services.git_integration_worker.cursor_sdk_events import (
    FrontierSdkAdmitWorkKeyInFlightRefused,
    FrontierSdkAdmitWorkKeyRequiredRefused,
)


def test_work_key_required_refusal_names_the_caller_and_contract() -> None:
    event = FrontierSdkAdmitWorkKeyRequiredRefused(
        dispatch_id="d1",
        thread_id="t1",
        contract="implement",
        caller_agent="cursor",
        mode="durable",
    )
    assert event.signal == "frontier.sdk.admit.work_key_required_refused"
    assert event.payload["dispatch_id"] == "d1"
    assert event.payload["thread_id"] == "t1"
    assert event.payload["contract"] == "implement"
    assert event.payload["caller_agent"] == "cursor"
    assert event.payload["mode"] == "durable"


def test_unattributed_caller_stays_an_explicit_null() -> None:
    """An absent caller must survive as None, not vanish from the payload.

    `caller_agent` is typed optional and an unattributed dispatch is exactly
    the case worth investigating, so a dropped key would hide the refusals
    that matter most.
    """
    event = FrontierSdkAdmitWorkKeyRequiredRefused(
        dispatch_id="d2",
        thread_id="t2",
        contract="none",
        caller_agent=None,
        mode="ephemeral",
    )
    assert "caller_agent" in event.payload
    assert event.payload["caller_agent"] is None


def test_in_flight_refusal_is_a_distinct_signal() -> None:
    """Missing a work_key and colliding with a live one are different faults.

    Both refuse admission, but only the second means a peer already holds the
    work; collapsing them would make a duplicate-dispatch bug read as a
    caller error.
    """
    required = FrontierSdkAdmitWorkKeyRequiredRefused(
        dispatch_id="d3",
        thread_id="t3",
        contract="implement",
        caller_agent="cursor",
        mode="durable",
    )
    in_flight = FrontierSdkAdmitWorkKeyInFlightRefused(
        dispatch_id="d3",
        thread_id="t3",
        work_key="todo:some-slug",
        identity_class="todo",
        contract="implement",
        caller_agent="cursor",
        holder_kind="conductor",
        mode="durable",
    )
    assert required.signal != in_flight.signal
    assert in_flight.signal == "frontier.sdk.admit.work_key_in_flight_refused"
    assert in_flight.payload["work_key"] == "todo:some-slug"
    assert in_flight.payload["holder_kind"] == "conductor"
    assert in_flight.payload["identity_class"] == "todo"
