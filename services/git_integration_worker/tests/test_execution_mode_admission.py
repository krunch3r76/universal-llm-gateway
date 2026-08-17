"""S-3 admission concurrency opt-in (mission 9440)."""

from __future__ import annotations

import uuid

import pytest

from services.git_integration_worker.cursor_auto.execution_mode import (
    _CONCURRENT_EXECUTION_MODES,
    is_concurrent_execution_mode,
)
from services.git_integration_worker.cursor_auto.job_ledger import (
    AutoJobLedger,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.queue import (
    AutoJob,
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


def _enqueue(
    queue: AutoJobQueue,
    *,
    execution_mode: str = "serial",
    thread_id: str = "9440",
    turn: int = 1,
) -> AutoJob:
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
        execution_mode=execution_mode,
    )


def test_default_execution_mode_is_serial_and_denied() -> None:
    assert is_concurrent_execution_mode("serial") is False
    assert is_concurrent_execution_mode(None) is False
    job = AutoJob(
        job_id=str(uuid.uuid4()),
        thread_id="t",
        turn_number=1,
        subject="s",
        body="b",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="answer",
    )
    assert job.execution_mode == "serial"


def test_production_allowlist_is_empty() -> None:
    assert _CONCURRENT_EXECUTION_MODES == frozenset()


def test_claim_next_skips_concurrent_class_leaves_it_for_concurrent_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.queue.is_concurrent_execution_mode",
        lambda mode: mode == "lease_free_test",
    )
    queue = get_queue()
    concurrent_job = _enqueue(queue, execution_mode="lease_free_test", turn=1)
    serial_job = _enqueue(queue, execution_mode="serial", turn=2)

    claimed = queue.claim_next()
    assert claimed is not None
    assert claimed.job_id == serial_job.job_id

    assert queue.claim_next() is None

    concurrent_claimed = queue.claim_next_concurrent()
    assert concurrent_claimed is not None
    assert concurrent_claimed.job_id == concurrent_job.job_id


def test_claim_next_concurrent_allows_second_claim_while_serial_occupied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.queue.is_concurrent_execution_mode",
        lambda mode: mode == "lease_free_test",
    )
    queue = get_queue()
    serial_job = _enqueue(queue, execution_mode="serial", turn=1)
    claimed_serial = queue.claim_next()
    assert claimed_serial is not None
    assert claimed_serial.job_id == serial_job.job_id
    assert claimed_serial.status == "claimed"

    concurrent_job = _enqueue(queue, execution_mode="lease_free_test", turn=2)
    concurrent_claimed = queue.claim_next_concurrent()
    assert concurrent_claimed is not None
    assert concurrent_claimed.job_id == concurrent_job.job_id


def test_execution_mode_round_trips_through_ledger() -> None:
    queue = get_queue()
    job = _enqueue(queue, execution_mode="lease_free_test")

    reset_queue_for_tests(durable=True)

    open_rows = get_ledger().list_open()
    matching = [row for row in open_rows if row.job_id == job.job_id]
    assert len(matching) == 1
    assert matching[0].execution_mode == "lease_free_test"
