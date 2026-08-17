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
from services.git_integration_worker.cursor_auto.terminal_reason_codec import (
    TERMINAL_REASON_RESTART_RECONCILE_SUPERSEDED,
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


def test_rehydrated_row_withdrawn_by_later_same_thread_request() -> None:
    """S-2(i): rehydrate a queued-never-claimed row, then a later same-thread
    request must withdraw it (method=queue_withdraw) and exactly one job runs.
    """
    from unittest.mock import AsyncMock, MagicMock

    from services.git_integration_worker.cursor_auto.supersede import (
        QUEUE_WITHDRAW,
        supersede_same_thread_inflight,
    )

    old_queue = get_queue()
    old = old_queue.enqueue(
        thread_id="9440-s2i",
        turn_number=1,
        subject="first",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )

    fresh_queue = reset_queue_for_tests(durable=True)
    asyncio.run(reconcile_open_auto_jobs(post_bus=False, rehydrate=True))

    rehydrated = fresh_queue.get(old.job_id)
    assert rehydrated is not None
    assert rehydrated.status == "queued"

    new = fresh_queue.enqueue(
        thread_id="9440-s2i",
        turn_number=2,
        subject="second",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    evidence = asyncio.run(
        supersede_same_thread_inflight(new, queue=fresh_queue, client=bus)
    )

    assert evidence is not None
    assert evidence["method"] == QUEUE_WITHDRAW
    assert fresh_queue.is_superseded(old.job_id)
    assert fresh_queue.get(old.job_id).status == "superseded"

    ran = fresh_queue.claim_next()
    assert ran is not None
    assert ran.job_id == new.job_id
    assert fresh_queue.claim_next() is None


def test_rehydrate_skipped_when_later_turn_successor_exists() -> None:
    """S-2(ii): a queued-never-claimed row whose thread already has a later
    turn_number row (any status) is terminalized, never requeued live.
    """
    queue = get_queue()
    old3 = queue.enqueue(
        thread_id="9440-s2ii-c",
        turn_number=1,
        subject="first",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    queue.enqueue(
        thread_id="9440-s2ii-c",
        turn_number=2,
        subject="second",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )

    fresh3 = reset_queue_for_tests(durable=True)
    asyncio.run(reconcile_open_auto_jobs(post_bus=False, rehydrate=True))

    assert fresh3.get(old3.job_id) is None
    row = get_ledger().read_relay_state(old3.job_id)
    assert row["status"] == "superseded"
    view = get_ledger().observer_state(job_id=old3.job_id)
    assert view is not None
    assert view["terminal_reason"] == TERMINAL_REASON_RESTART_RECONCILE_SUPERSEDED
    ledger_row = get_ledger().read_record_json(old3.job_id)
    assert ledger_row.get("rehydrate_superseded_by") is not None


def test_rehydrate_skipped_when_later_turn_successor_is_still_queued() -> None:
    """S-2(ii) variant: successor still queued (live) triggers the same gate."""
    queue = get_queue()
    old = queue.enqueue(
        thread_id="9440-s2ii-live",
        turn_number=1,
        subject="first",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    successor = queue.enqueue(
        thread_id="9440-s2ii-live",
        turn_number=2,
        subject="second",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    assert old.status == "queued"
    assert successor.status == "queued"

    fresh = reset_queue_for_tests(durable=True)
    asyncio.run(reconcile_open_auto_jobs(post_bus=False, rehydrate=True))

    assert fresh.get(old.job_id) is None
    assert fresh.get(successor.job_id) is not None
    assert fresh.get(successor.job_id).status == "queued"
    row = get_ledger().read_relay_state(old.job_id)
    assert row["status"] == "superseded"


def test_rehydrate_happy_path_requeues_queued_row() -> None:
    """S-1: queued-never-claimed row survives restart and re-enters live FIFO."""
    queue = get_queue()
    job = queue.enqueue(
        thread_id="9440-s1",
        turn_number=1,
        subject="solo",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )

    fresh = reset_queue_for_tests(durable=True)
    asyncio.run(reconcile_open_auto_jobs(post_bus=False, rehydrate=True))

    rehydrated = fresh.get(job.job_id)
    assert rehydrated is not None
    assert rehydrated.status == "queued"
    record = get_ledger().read_record_json(job.job_id)
    assert record.get("rehydrated") is True
    assert record.get("generation") == 1
    assert fresh.claim_next().job_id == job.job_id

