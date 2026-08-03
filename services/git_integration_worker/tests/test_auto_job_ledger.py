"""Acceptance tests for durable cursor-auto job ledger (G3 / a:27554)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

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
    queue = get_queue()
    jobs = [_enqueue(queue, turn=i) for i in range(1, 4)]
    queue.claim_next()

    with patch(
        "services.git_integration_worker.cursor_auto.job_reconcile."
        "post_queue_owner_restart_terminal",
        new_callable=AsyncMock,
    ) as post_terminal:
        terminalized = asyncio.run(reconcile_open_auto_jobs(post_bus=True))

    assert len(terminalized) == 3
    assert post_terminal.await_count == 3
    ledger = get_ledger()
    for job in jobs:
        row = ledger.mark_terminal(
            job.job_id,
            status="failed",
            terminal_reason=TERMINAL_REASON_QUEUE_OWNER_RESTART,
        )
        assert row is None
    open_rows = ledger.list_open()
    assert open_rows == []


def test_startup_reconcile_idempotent_no_duplicate_bus_posts() -> None:
    queue = get_queue()
    _enqueue(queue, turn=1)
    _enqueue(queue, turn=2)

    with patch(
        "services.git_integration_worker.cursor_auto.job_reconcile."
        "post_queue_owner_restart_terminal",
        new_callable=AsyncMock,
    ) as post_terminal:
        first = asyncio.run(reconcile_open_auto_jobs(post_bus=True))
        second = asyncio.run(reconcile_open_auto_jobs(post_bus=True))

    assert len(first) == 2
    assert second == []
    assert post_terminal.await_count == 2


def test_queue_snapshot_exposes_failed_on_restart() -> None:
    queue = get_queue()
    job = _enqueue(queue)
    asyncio.run(reconcile_open_auto_jobs(post_bus=False))

    snap = queue.snapshot()
    assert snap["failed_on_restart"] == 1
    assert snap["pending"] == 0
    assert snap["claimed"] == 0
    assert queue.get(job.job_id) is not None


def test_claimed_heartbeat_advances_durable_timestamp() -> None:
    queue = get_queue()
    job = _enqueue(queue)
    claimed = queue.claim_next()
    assert claimed is not None
    ledger = get_ledger()
    before = ledger._connect().execute(  # noqa: SLF001
        "SELECT last_heartbeat_at FROM cursor_auto_jobs WHERE job_id=?",
        (job.job_id,),
    ).fetchone()["last_heartbeat_at"]
    queue.bump_heartbeat(job.job_id)
    after = ledger._connect().execute(  # noqa: SLF001
        "SELECT last_heartbeat_at FROM cursor_auto_jobs WHERE job_id=?",
        (job.job_id,),
    ).fetchone()["last_heartbeat_at"]
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
        terminalized = asyncio.run(reconcile_open_auto_jobs(post_bus=True))

    assert len(terminalized) == 1
    assert terminalized[0].job_id == job.job_id
    assert post_terminal.await_count == 1
    assert ledger.list_open() == []
