"""Tests for cursor-auto relay admission ticket (drain tail)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.cursor_auto.job_ledger import AutoJobLedger
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


def test_relay_ticket_keeps_active_count_until_closeout_done() -> None:
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
