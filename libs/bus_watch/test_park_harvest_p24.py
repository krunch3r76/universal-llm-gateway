"""P2.4 hygiene tests for bus_watch park_harvest helpers."""

from __future__ import annotations

from bus_watch.park_harvest import successor_owed

_CONSULT_PENDING_WAIT = """\
CONSULT_PENDING
execution_id: exec-abc
poll_hint: wait
NEXT_ADMIT: G5
"""


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
