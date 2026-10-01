"""Relay admission ticket lifecycle: the drain tail, the submission-failure
release path, and the leaked-ticket reap that keeps a lost reservation from
wedging drain convergence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger


def _relay_ticket(controller: WorkAdmissionController, op_id: str, *, age_s: float):
    ticket = controller.try_admit(
        "cursor-auto", op_id=op_id, route="cursor-auto/nested"
    )
    ticket.admitted_at = datetime.now(UTC) - timedelta(seconds=age_s)
    return ticket


def _controller() -> WorkAdmissionController:
    return WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="w1",
        pid=1,
        worker_started_at="t",
    )


def test_leaked_relay_ticket_is_reaped_once_past_grace() -> None:
    """An aged relay ticket with no ledger row stops wedging ``active_count``."""
    controller = _controller()
    _relay_ticket(controller, "auto-leaked01", age_s=5000)

    assert controller.active_count() == 0
    assert controller.active_ops() == []


def test_relay_ticket_inside_grace_still_counts() -> None:
    """The reap never fires during the reserve-then-insert window."""
    controller = _controller()
    _relay_ticket(controller, "auto-fresh001", age_s=5)

    assert controller.active_count() == 1


def test_relay_ticket_with_ledger_row_is_never_reaped() -> None:
    """A dispatch the worker accepted keeps its ticket however old it gets."""
    controller = _controller()
    _relay_ticket(controller, "auto-live0001", age_s=5000)

    with patch.object(
        CursorDispatchLedger,
        "dispatch_status_by_id",
        return_value={"dispatch_id": "auto-live0001", "status": "queued"},
    ):
        assert controller.active_count() == 1


def test_running_relay_ticket_is_never_reaped() -> None:
    """Only ``pending`` reservations are reapable; running work is untouchable."""
    controller = _controller()
    ticket = _relay_ticket(controller, "auto-running1", age_s=5000)
    ticket.mark_running()

    assert controller.active_count() == 1


def test_non_relay_ticket_is_never_reaped() -> None:
    """Integrate tickets have no ledger counterpart and must not be reaped."""
    controller = _controller()
    ticket = controller.try_admit("git_integrate", op_id="int-0001", route="/integrate")
    ticket.admitted_at = datetime.now(UTC) - timedelta(seconds=5000)

    assert controller.active_count() == 1


def test_relay_ticket_keeps_active_count_until_closeout_done() -> None:
    """Verify closing a relay ticket removes the final active admission record."""
    ledger = CursorDispatchLedger.instance()
    controller = WorkAdmissionController(
        ledger=ledger,
        worker_id="w1",
        pid=1,
        worker_started_at="t",
    )
    dispatch_id = "auto-ticket001"
    controller.try_admit("cursor-auto", op_id=dispatch_id, route="cursor-auto/nested")
    assert controller.active_count() == 1
    controller.close_ticket(dispatch_id, terminal_status="completed")
    assert controller.active_count() == 0
