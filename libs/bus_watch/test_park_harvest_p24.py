"""P2.4 hygiene tests for bus_watch park_harvest helpers."""

from __future__ import annotations

from bus_watch.park_harvest import harvest_still_owed, successor_owed

_CONSULT_PENDING_WAIT = """\
CONSULT_PENDING
execution_id: exec-abc
poll_hint: wait
NEXT_ADMIT: G5
"""

_PARKED_TRANSPORT_NO_NEXT_ADMIT = """\
status: complete
stop: PARKED_TRANSPORT
CONSULT_PENDING
execution_id: exec-abc
poll_hint: wait
"""


def test_harvest_still_owed_parked_transport_without_next_admit() -> None:
    assert harvest_still_owed(body=_PARKED_TRANSPORT_NO_NEXT_ADMIT)


def test_harvest_still_owed_parked_transport_only() -> None:
    body = "status: complete\nstop: PARKED_TRANSPORT\n"
    assert harvest_still_owed(body=body)


def test_successor_owed_false_on_consult_pending_wait_p24() -> None:
    assert (
        successor_owed(
            closeout_tokens=frozenset({"ROW_HOP", "CONSULT_PENDING"}),
            closeout_body=_CONSULT_PENDING_WAIT,
        )
        is False
    )


def test_successor_owed_true_on_row_hop_without_consult() -> None:
    assert successor_owed(closeout_tokens=frozenset({"ROW_HOP"})) is True
