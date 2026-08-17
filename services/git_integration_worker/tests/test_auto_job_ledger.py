"""Acceptance tests for durable cursor-auto job ledger (G3 / a:27554)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.git_integration_worker.cursor_auto.job_ledger import (
    TERMINAL_REASON_QUEUE_OWNER_RESTART,
    AutoJobLedger,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.job_reconcile import (
    reconcile_open_auto_jobs,
)
from services.git_integration_worker.cursor_auto.queue import (
    AutoJobQueue,
    get_queue,
    reset_queue_for_tests,
)


@pytest.fixture(autouse=True)
def _isolated_auto_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    AutoJobLedger.reset_for_tests()
    reset_queue_for_tests(durable=True)
    yield
    AutoJobLedger.reset_for_tests()


def _enqueue(queue: AutoJobQueue, *, thread_id: str = "6701", turn: int = 1):
    return queue.enqueue(
        thread_id=thread_id,
        turn_number=turn,
        subject=f"turn {turn}",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="investigate",
    )


def test_shutdown_reconcile_terminalizes_open_jobs() -> None:
    """Shutdown reconcile (``rehydrate=False``, the default at that call
    site) terminalizes only the claimed row. Queued-never-claimed rows are
    left durable untouched — they are the startup rehydrate path's (S-1)
    responsibility, not shutdown's; see ``_reconcile_queued_job``."""
    queue = get_queue()
    jobs = [_enqueue(queue, turn=i) for i in range(1, 4)]
    claimed = queue.claim_next()
    assert claimed is not None

    with patch(
        "services.git_integration_worker.cursor_auto.job_reconcile."
        "post_queue_owner_restart_terminal",
        new_callable=AsyncMock,
    ) as post_terminal:
        terminalized = asyncio.run(reconcile_open_auto_jobs(post_bus=True))

    assert len(terminalized) == 1
    assert terminalized[0].job_id == claimed.job_id
    assert post_terminal.await_count == 1
    ledger = get_ledger()
    row = ledger.mark_terminal(
        claimed.job_id,
        status="failed",
        terminal_reason=TERMINAL_REASON_QUEUE_OWNER_RESTART,
    )
    assert row is None
    open_rows = ledger.list_open()
    assert {row.job_id for row in open_rows} == {
        job.job_id for job in jobs if job.job_id != claimed.job_id
    }


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Confirmed gap (mission 9440 turn 82 AC4 followup, filed as "
        "cortex friction): queue.requeue_rehydrated() is documented "
        "idempotent against a reconcile that 'runs twice against the same "
        "durable row' (job_id already resident -> no-op), but "
        "_reconcile_queued_job's generation bump + "
        "post_queue_owner_restart_recovered notify are NOT gated on that "
        "same residency check, so a second rehydrate=True pass against a "
        "still-queued row double-bumps generation and double-posts the "
        "recovered notify. Flip to a plain assertion (drop xfail) once "
        "job_reconcile guards the rehydrate side-effects on queue "
        "residency, not just queue.requeue_rehydrated's own no-op."
    ),
)
def test_startup_reconcile_idempotent_no_duplicate_bus_posts() -> None:
    """Startup reconcile (``rehydrate=True``) must not double-rehydrate or
    double-notify a queued-never-claimed row if it somehow runs twice
    against the same durable row before the row is claimed."""
    queue = get_queue()
    _enqueue(queue, turn=1)
    _enqueue(queue, turn=2)

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    with patch(
        "services.git_integration_worker.cursor_auto.job_reconcile.CursorBusClient",
        return_value=bus,
    ):
        first = asyncio.run(reconcile_open_auto_jobs(post_bus=True, rehydrate=True))
        second = asyncio.run(reconcile_open_auto_jobs(post_bus=True, rehydrate=True))

    assert first == []
    assert second == []
    assert bus.reply.await_count == 2


def test_queue_snapshot_shows_rehydrated_not_failed_on_restart() -> None:
    """S-1: a lone queued-never-claimed job surviving a restart must show up
    as still pending, not as ``failed_on_restart`` — that status/reason
    combination is exactly the queue_owner_restart dead-lettering S-1
    replaced with rehydrate-and-requeue. (Formerly
    ``test_queue_snapshot_exposes_failed_on_restart``, which asserted the
    pre-S-1 dead-letter outcome this rehydrate path supersedes.)"""
    queue = get_queue()
    job = _enqueue(queue)
    asyncio.run(reconcile_open_auto_jobs(post_bus=False, rehydrate=True))

    snap = queue.snapshot()
    assert snap["failed_on_restart"] == 0
    assert snap["pending"] == 1
    assert snap["claimed"] == 0
    assert queue.get(job.job_id) is not None
    record = get_ledger().read_record_json(job.job_id)
    assert record.get("rehydrated") is True
    assert record.get("generation") == 1


def test_claimed_heartbeat_advances_durable_timestamp() -> None:
    queue = get_queue()
    job = _enqueue(queue)
    claimed = queue.claim_next()
    assert claimed is not None
    ledger = get_ledger()
    before = (
        ledger._connect()
        .execute(  # noqa: SLF001
            "SELECT last_heartbeat_at FROM cursor_auto_jobs WHERE job_id=?",
            (job.job_id,),
        )
        .fetchone()["last_heartbeat_at"]
    )
    queue.bump_heartbeat(job.job_id)
    after = (
        ledger._connect()
        .execute(  # noqa: SLF001
            "SELECT last_heartbeat_at FROM cursor_auto_jobs WHERE job_id=?",
            (job.job_id,),
        )
        .fetchone()["last_heartbeat_at"]
    )
    assert after is not None
    assert before is not None
    assert after >= before


def test_happy_path_enqueue_claim_done() -> None:
    queue = get_queue()
    job = _enqueue(queue)
    claimed = queue.claim_next()
    assert claimed is not None
    queue.mark_done(job.job_id, failed=False)

    ledger = get_ledger()
    assert ledger.list_open() == []
    row = ledger.mark_terminal(job.job_id, status="failed", terminal_reason="x")
    assert row is None
    snap = queue.snapshot()
    assert snap["done"] == 1
    assert snap.get("failed_on_restart", 0) == 0


def test_hard_kill_backstop_startup_reconcile() -> None:
    """A row that only exists in the durable ledger (inserted directly,
    bypassing the live queue instance's own bookkeeping — the hard-kill
    signature: the process died before the live queue and ledger stayed in
    sync) is picked up by ``_open_jobs_union`` via the ledger side and,
    under S-1, rehydrated like any other queued-never-claimed row rather
    than dead-lettered — it is durable, so the work is not lost."""
    ledger = get_ledger()
    job = AutoJobQueue(durable=False).enqueue(
        thread_id="6701",
        turn_number=9,
        subject="orphan",
        body="",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    ledger.insert(job)

    with patch(
        "services.git_integration_worker.cursor_auto.job_reconcile."
        "post_queue_owner_restart_terminal",
        new_callable=AsyncMock,
    ) as post_terminal:
        terminalized = asyncio.run(
            reconcile_open_auto_jobs(post_bus=True, rehydrate=True)
        )

    assert terminalized == []
    assert post_terminal.await_count == 0
    open_rows = ledger.list_open()
    assert len(open_rows) == 1
    assert open_rows[0].job_id == job.job_id
    record = ledger.read_record_json(job.job_id)
    assert record.get("rehydrated") is True
    assert record.get("generation") == 1
    assert get_queue().get(job.job_id) is not None
