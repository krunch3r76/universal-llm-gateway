"""Tests for cursor-auto job reconcile triage branches."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.git_integration_worker.cursor_auto.closeout_outbox import (
    CloseoutOutboxStore,
    get_outbox_store,
)
from services.git_integration_worker.cursor_auto.job_ledger import (
    AutoJobLedger,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.job_reconcile import (
    reconcile_open_auto_jobs,
)
from services.git_integration_worker.cursor_auto.queue import (
    get_queue,
    reset_queue_for_tests,
)


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    AutoJobLedger.reset_for_tests()
    CloseoutOutboxStore.reset_for_tests()
    reset_queue_for_tests(durable=True)
    yield
    AutoJobLedger.reset_for_tests()
    CloseoutOutboxStore.reset_for_tests()


def _enqueue(*, turn: int = 1):
    return get_queue().enqueue(
        thread_id="6701",
        turn_number=turn,
        subject=f"turn {turn}",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )


def test_never_dispatched_posts_queue_owner_restart() -> None:
    job = _enqueue()
    get_queue().claim_next()

    with patch(
        "services.git_integration_worker.cursor_auto.job_reconcile."
        "post_queue_owner_restart_terminal",
        new_callable=AsyncMock,
    ) as post_terminal:
        terminalized = asyncio.run(reconcile_open_auto_jobs(post_bus=True))

    assert len(terminalized) == 1
    assert post_terminal.await_count == 1
    row = get_ledger().read_relay_state(job.job_id)
    assert row["status"] == "failed"


def test_delivered_outbox_suppresses_loss_report() -> None:
    job = _enqueue()
    get_queue().claim_next()
    get_outbox_store().persist_pending(
        dispatch_id="auto-abc123",
        job_id=job.job_id,
        thread_id="6701",
        to_agent="web-anthropic",
        from_agent="cursor-auto",
        subject="status:done",
        envelope_body="TYPE: CLOSEOUT\nstatus: complete\n",
        closeout_status="complete",
        request_turn=1,
        worker_id="w1",
        worker_started_at="t",
    )
    get_outbox_store().mark_posted("auto-abc123")

    with patch(
        "services.git_integration_worker.cursor_auto.job_reconcile."
        "post_queue_owner_restart_terminal",
        new_callable=AsyncMock,
    ) as post_terminal:
        terminalized = asyncio.run(reconcile_open_auto_jobs(post_bus=True))

    assert len(terminalized) == 1
    assert post_terminal.await_count == 0
    row = get_ledger().read_relay_state(job.job_id)
    assert row["status"] == "done"


def test_pending_outbox_defers_terminalize() -> None:
    job = _enqueue()
    get_queue().claim_next()
    get_outbox_store().persist_pending(
        dispatch_id="auto-pending1",
        job_id=job.job_id,
        thread_id="6701",
        to_agent="web-anthropic",
        from_agent="cursor-auto",
        subject="status:done",
        envelope_body="TYPE: CLOSEOUT\nstatus: complete\n",
        closeout_status="complete",
        request_turn=1,
        worker_id="w1",
        worker_started_at="t",
    )

    with patch(
        "services.git_integration_worker.cursor_auto.job_reconcile."
        "post_queue_owner_restart_terminal",
        new_callable=AsyncMock,
    ) as post_terminal:
        terminalized = asyncio.run(reconcile_open_auto_jobs(post_bus=True))

    assert terminalized == []
    assert post_terminal.await_count == 0
    assert get_ledger().list_open()
