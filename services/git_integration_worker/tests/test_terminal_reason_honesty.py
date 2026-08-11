"""Arc 6655 — terminal_reason honesty on failed cursor-auto jobs."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from implement_admission.propagation_row import PropagationRow
from services.git_integration_worker.cursor_auto.handler_propagation import (
    execution_for_manage_deferred,
)
from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
    terminal_expired,
)
from services.git_integration_worker.cursor_auto.job_ledger import (
    TERMINAL_REASON_QUEUE_OWNER_RESTART,
    AutoJobLedger,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.job_reconcile import (
    reconcile_open_auto_jobs,
)
from services.git_integration_worker.cursor_auto.queue import (
    AutoJob,
    AutoJobQueue,
    get_queue,
    reset_queue_for_tests,
)
from services.git_integration_worker.cursor_auto.terminal_post_outcome import (
    TERMINAL_REASON_BUS_TRANSPORT,
    terminal_reason_for_status,
)
from services.git_integration_worker.cursor_auto.terminal_reason_codec import (
    TERMINAL_REASON_RECONCILE_INFLIGHT_LOST,
    format_exception_reason,
)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    AutoJobLedger.reset_for_tests()
    reset_queue_for_tests(durable=True)
    yield
    AutoJobLedger.reset_for_tests()


def _job(*, job_id: str = "job-reason") -> AutoJob:
    return AutoJob(
        job_id=job_id,
        thread_id="6655",
        turn_number=1312,
        subject="propagate restart",
        body="TYPE: DIRECTIVE\ncontract: propagate\n",
        from_agent="cdp-operator-6655",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="propagate",
        request_id="req-6655-1312",
    )


def _enqueue(queue: AutoJobQueue | None = None) -> AutoJob:
    q = queue or get_queue()
    return q.enqueue(
        thread_id="6655",
        turn_number=1312,
        subject="propagate",
        body="TYPE: DIRECTIVE\n",
        from_agent="cdp-operator",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="propagate",
    )


def test_table_worker_exception_reason_includes_type() -> None:
    exc = NameError("name 'set_defer_reason' is not defined")
    reason = format_exception_reason(exc)
    assert reason.startswith("NameError:")
    assert "set_defer_reason" in reason


def test_table_deliberate_failure_uses_payload_reason() -> None:
    from services.git_integration_worker.cursor_auto.terminal_reason_codec import (
        deliberate_failure_terminal_reason,
    )

    reason = deliberate_failure_terminal_reason(
        disposition="blocked",
        payload={"reason": "expired"},
        summary="ignored when reason present",
    )
    assert reason == "expired"


@pytest.mark.asyncio
async def test_table_deliberate_failure_via_terminal_post_status() -> None:
    queue = get_queue()
    job = _enqueue(queue)
    queue.claim_next()
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=201, body={}))
    await terminal_expired(
        job,
        client=client,
        queue=queue,
        deadline="2026-08-11T00:00:00Z",
        elapsed_s=120.0,
    )
    view = get_ledger().observer_state(job_id=job.job_id)
    assert view is not None
    assert view["status"] == "failed"
    assert view["terminal_reason"] == "expired"


@pytest.mark.asyncio
async def test_table_queue_owner_restart_reason() -> None:
    queue = get_queue()
    job = _enqueue(queue)
    queue.claim_next()
    with patch(
        "services.git_integration_worker.cursor_auto.job_reconcile."
        "post_queue_owner_restart_terminal",
        new_callable=AsyncMock,
        return_value={"status_code": 201},
    ):
        terminalized = await reconcile_open_auto_jobs(post_bus=True)
    assert len(terminalized) == 1
    view = get_ledger().observer_state(job_id=job.job_id)
    assert view is not None
    assert view["terminal_reason"] == TERMINAL_REASON_QUEUE_OWNER_RESTART


@pytest.mark.asyncio
async def test_table_bus_reject_reason_distinct_from_transport() -> None:
    queue = get_queue()
    job = _enqueue(queue)
    queue.claim_next()
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=413, body={}))
    await post_terminal_status(
        _job(job_id=job.job_id),
        client=client,
        queue=queue,
        summary="propagated",
        disposition="propagated",
        contract="propagate",
        payload={"summary": "propagated"},
    )
    view = get_ledger().observer_state(job_id=job.job_id)
    assert view is not None
    assert view["status"] == "report_undelivered"
    assert view["terminal_reason"] == terminal_reason_for_status(413)
    assert view["terminal_reason"] != TERMINAL_REASON_BUS_TRANSPORT


@pytest.mark.asyncio
async def test_table_transport_error_reason() -> None:
    queue = get_queue()
    job = _enqueue(queue)
    queue.claim_next()
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=503, body={}))
    await post_terminal_status(
        _job(job_id=job.job_id),
        client=client,
        queue=queue,
        summary="propagated",
        disposition="propagated",
        contract="propagate",
        payload={"summary": "propagated"},
    )
    view = get_ledger().observer_state(job_id=job.job_id)
    assert view is not None
    assert view["terminal_reason"] == TERMINAL_REASON_BUS_TRANSPORT
    assert view["terminal_reason"] != terminal_reason_for_status(413)


def test_table_rows_do_not_collapse() -> None:
    reasons = {
        format_exception_reason(NameError("set_defer_reason")),
        "expired",
        TERMINAL_REASON_QUEUE_OWNER_RESTART,
        terminal_reason_for_status(413),
        TERMINAL_REASON_BUS_TRANSPORT,
        TERMINAL_REASON_RECONCILE_INFLIGHT_LOST,
    }
    assert len(reasons) == 6


def test_mark_done_failed_requires_terminal_reason() -> None:
    queue = AutoJobQueue(durable=False)
    job = queue.enqueue(
        thread_id="6655",
        turn_number=1,
        subject="s",
        body="b",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="propagate",
    )
    queue.claim_next()
    with pytest.raises(ValueError, match="terminal_reason required"):
        queue.mark_done(job.job_id, failed=True)


def test_ledger_mark_terminal_failed_requires_terminal_reason() -> None:
    queue = get_queue()
    job = _enqueue(queue)
    queue.claim_next()
    ledger = get_ledger()
    with pytest.raises(ValueError, match="terminal_reason required"):
        ledger.mark_terminal(job.job_id, status="failed", terminal_reason=None)


def test_failed_job_observer_state_never_null_terminal_reason() -> None:
    queue = get_queue()
    job = _enqueue(queue)
    queue.claim_next()
    queue.mark_done(
        job.job_id,
        failed=True,
        terminal_reason=format_exception_reason(RuntimeError("boom")),
    )
    view = get_ledger().observer_state(job_id=job.job_id)
    assert view is not None
    assert view["status"] == "failed"
    assert view["terminal_reason"] is not None


@pytest.mark.asyncio
async def test_worker_loop_persists_name_error_terminal_reason() -> None:
    from services.git_integration_worker.cursor_auto import auto_worker_loop as mod
    from services.git_integration_worker.cursor_auto.liveness import AutoLivenessRegistry

    registry = AutoLivenessRegistry()
    app = SimpleNamespace(
        state=SimpleNamespace(
            admission_controller=None, worker_id="w", worker_boot_ts="t"
        )
    )
    queue = get_queue()
    job = _enqueue(queue)

    async def _raise_name_error(*_a, **_k):
        raise NameError("name 'set_defer_reason' is not defined")

    with (
        patch.object(mod, "get_registry", return_value=registry),
        patch.object(mod, "get_queue", return_value=queue),
        patch.object(mod, "process_job", _raise_name_error),
        patch.object(mod, "_WORKER_INTERVAL_S", 0.01),
    ):
        task = asyncio.create_task(mod.auto_worker_loop(app))
        await asyncio.sleep(0.12)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    view = get_ledger().observer_state(job_id=job.job_id)
    assert view is not None
    assert view["status"] == "failed"
    assert view["terminal_reason"].startswith("NameError:")
    assert "set_defer_reason" in view["terminal_reason"]


def test_execution_for_manage_deferred_import_resolves_set_defer_reason() -> None:
    row = PropagationRow(
        service="git_integration_worker",
        code_ref="deadbeef",
        action="sync_restart",
        proof_class="process_live",
    )
    row_id = "git_integration_worker:deadbeef:sync_restart"
    with patch(
        "services.git_integration_worker.cursor_auto.handler_propagation.set_defer_reason",
    ) as mock_set:
        result = execution_for_manage_deferred(
            row,
            row_id=row_id,
            manage_result={
                "status": "deferred",
                "state": "draining",
                "restart_intent_id": "intent-abc",
                "reason": "draining",
            },
        )
    assert result["status"] == "queued"
    mock_set.assert_called_once_with(row_id, "manage_queued_drain")


def test_relay_closeout_outcome_load_config_import_resolves() -> None:
    """6655 undefined-name sweep: relay path must bind load_config at import."""
    from services.git_integration_worker.config import load_config as config_load
    from services.git_integration_worker.cursor_auto import nested_outcome

    assert nested_outcome.load_config is config_load
