"""Relay admission ticket lifecycle: the drain tail, the submission-failure
release path, and the leaked-ticket reap that keeps a lost reservation from
wedging drain convergence.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.cursor_auto.job_ledger import (
    RELAY_PHASE_NONE,
    AutoJobLedger,
)
from services.git_integration_worker.cursor_auto.nested_sdk import CloseoutRelayContext
from services.git_integration_worker.cursor_auto.queue import (
    AutoJob,
    get_queue,
    reset_queue_for_tests,
)
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    AutoJobLedger.reset_for_tests()
    reset_queue_for_tests(durable=True)
    yield
    AutoJobLedger.reset_for_tests()


def _job() -> AutoJob:
    return get_queue().enqueue(
        thread_id="6701",
        turn_number=1,
        subject="ticket test",
        body="TYPE: DIRECTIVE\ncontract: implement\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )


def test_submit_try_admit_does_not_double_count_with_ledger_projection() -> None:
    """Verify a successful SDK admission remains counted until its closeout arrives."""
    from services.git_integration_worker.cursor_auto.nested_sdk import (
        submit_nested_dispatch,
    )

    ledger = CursorDispatchLedger.instance()
    controller = WorkAdmissionController(
        ledger=ledger,
        worker_id="w1",
        pid=1,
        worker_started_at="t",
    )
    job = _job()
    ctx = CloseoutRelayContext(
        worker_id="w1",
        worker_started_at="t",
        admission_controller=controller,
    )

    with patch(
        "services.git_integration_worker.cursor_auto.nested_sdk.httpx.AsyncClient"
    ) as client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"admitted": true}'
        mock_resp.json.return_value = {"admitted": True}
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        client_cls.return_value = mock_client

        result = asyncio.run(
            submit_nested_dispatch(
                job,
                model_id="auto",
                handoff_contract="implement",
                message="go",
                relay_ctx=ctx,
            )
        )

    assert result.get("ok") is True
    dispatch_id = str(result["dispatch_id"])
    assert controller.active_count() == 1
    controller.try_admit("cursor-auto", op_id=dispatch_id, route="cursor-auto/nested")
    assert controller.active_count() == 1


@pytest.mark.parametrize("failure_kind", ["http", "transport"])
def test_failed_nested_submit_closes_relay_ticket(failure_kind: str) -> None:
    """Verify a failed nested submission releases its relay admission ticket."""
    from services.git_integration_worker.cursor_auto.nested_sdk import (
        submit_nested_dispatch,
    )

    ledger = CursorDispatchLedger.instance()
    controller = WorkAdmissionController(
        ledger=ledger,
        worker_id="w1",
        pid=1,
        worker_started_at="t",
    )
    job = _job()
    ctx = CloseoutRelayContext(
        worker_id="w1",
        worker_started_at="t",
        admission_controller=controller,
    )

    with patch(
        "services.git_integration_worker.cursor_auto.nested_sdk.httpx.AsyncClient"
    ) as client_cls:
        mock_client = AsyncMock()
        if failure_kind == "http":
            mock_resp = MagicMock()
            mock_resp.status_code = 503
            mock_resp.content = b'{"detail":"worktree mint failed"}'
            mock_resp.json.return_value = {"detail": "worktree mint failed"}
            mock_client.post = AsyncMock(return_value=mock_resp)
        else:
            mock_client.post = AsyncMock(
                side_effect=httpx.HTTPError("worker unavailable")
            )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        client_cls.return_value = mock_client

        result = asyncio.run(
            submit_nested_dispatch(
                job,
                model_id="auto",
                handoff_contract="implement",
                message="go",
                relay_ctx=ctx,
            )
        )

    assert result["ok"] is False
    assert controller.active_ops() == []
    relay = AutoJobLedger.instance().read_relay_state(job.job_id)
    assert relay["relay_phase"] == RELAY_PHASE_NONE


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
