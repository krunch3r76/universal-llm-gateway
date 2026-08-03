"""Tests for cursor-auto closeout outbox boot replay."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.git_integration_worker.cursor_auto.closeout_outbox import (
    CloseoutOutboxStore,
    get_outbox_store,
)
from services.git_integration_worker.cursor_auto.closeout_replay import (
    startup_closeout_outbox_replay,
)
from services.git_integration_worker.cursor_auto.job_ledger import (
    AutoJobLedger,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.queue import (
    get_queue,
    reset_queue_for_tests,
)
from services.git_integration_worker.cursor_bus import BusReplyResult


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    AutoJobLedger.reset_for_tests()
    CloseoutOutboxStore.reset_for_tests()
    reset_queue_for_tests(durable=True)
    yield
    AutoJobLedger.reset_for_tests()
    CloseoutOutboxStore.reset_for_tests()


def _enqueue(*, thread_id: str = "6701", turn: int = 1):
    return get_queue().enqueue(
        thread_id=thread_id,
        turn_number=turn,
        subject="relay test",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )


def _persist_row(
    *,
    dispatch_id: str = "auto-deadbeef001",
    job_id: str,
    worker_id: str = "old-worker",
) -> None:
    store = get_outbox_store()
    store.persist_pending(
        dispatch_id=dispatch_id,
        job_id=job_id,
        thread_id="6701",
        to_agent="web-anthropic",
        from_agent="cursor-auto",
        subject="status:done — relay test",
        envelope_body=(
            "TYPE: CLOSEOUT\n"
            "status: complete\n"
            f"dispatch_id: {dispatch_id}\n"
            "model: auto\n"
            "request_turn: 1\n"
            "checkpoint: nothing_authored\n"
            "\n"
            "status: complete\n"
        ),
        closeout_status="complete",
        request_turn=1,
        worker_id=worker_id,
        worker_started_at="2026-08-03T00:00:00+00:00",
        checkpoint_value="nothing_authored",
        tree_residue=0,
    )


class _App:
    state: object

    def __init__(self, worker_id: str) -> None:
        self.state = type("S", (), {"worker_id": worker_id, "worker_boot_ts": "t"})()


def test_replay_discards_superseded_job() -> None:
    job = _enqueue()
    get_ledger().bind_dispatch(job.job_id, dispatch_id="auto-deadbeef001")
    get_ledger().mark_terminal(job.job_id, status="superseded", terminal_reason="superseded")
    _persist_row(job_id=job.job_id)

    with patch(
        "services.git_integration_worker.cursor_auto.closeout_replay.fetch_turns_from",
        new_callable=AsyncMock,
        return_value=([], None),
    ):
        asyncio.run(startup_closeout_outbox_replay(_App("new-worker")))

    row = get_outbox_store().get("auto-deadbeef001")
    assert row is not None
    assert row.state == "discarded"


def test_replay_skips_when_bus_scan_finds_closeout() -> None:
    job = _enqueue()
    get_ledger().bind_dispatch(job.job_id, dispatch_id="auto-deadbeef001")
    _persist_row(job_id=job.job_id)
    turn = {
        "from": "cursor-auto",
        "turn_number": 2,
        "body": "TYPE: CLOSEOUT\ndispatch_id: auto-deadbeef001\n",
    }

    with patch(
        "services.git_integration_worker.cursor_auto.closeout_replay.fetch_turns_from",
        new_callable=AsyncMock,
        return_value=([turn], None),
    ):
        asyncio.run(startup_closeout_outbox_replay(_App("new-worker")))

    row = get_outbox_store().get("auto-deadbeef001")
    assert row is not None
    assert row.state == "posted_confirmed"


def test_replay_defers_when_bus_unreachable() -> None:
    job = _enqueue()
    get_ledger().bind_dispatch(job.job_id, dispatch_id="auto-deadbeef001")
    _persist_row(job_id=job.job_id)

    with patch(
        "services.git_integration_worker.cursor_auto.closeout_replay.fetch_turns_from",
        new_callable=AsyncMock,
        return_value=(None, "bus_down"),
    ):
        asyncio.run(startup_closeout_outbox_replay(_App("new-worker")))

    row = get_outbox_store().get("auto-deadbeef001")
    assert row is not None
    assert row.state == "pending"
    assert row.attempts == 1


def test_generation_guard_skips_same_worker_rows() -> None:
    job = _enqueue()
    worker = "same-worker"
    _persist_row(job_id=job.job_id, worker_id=worker)

    with patch(
        "services.git_integration_worker.cursor_auto.closeout_replay.fetch_turns_from",
        new_callable=AsyncMock,
    ) as fetch:
        asyncio.run(startup_closeout_outbox_replay(_App(worker)))
        fetch.assert_not_called()

    row = get_outbox_store().get("auto-deadbeef001")
    assert row is not None
    assert row.state == "pending"


def test_replay_abandons_after_max_attempts() -> None:
    job = _enqueue()
    get_ledger().bind_dispatch(job.job_id, dispatch_id="auto-deadbeef001")
    _persist_row(job_id=job.job_id)
    store = get_outbox_store()
    for _ in range(2):
        store.increment_attempts("auto-deadbeef001")

    mock_client = AsyncMock()
    mock_client.reply = AsyncMock(
        return_value=BusReplyResult(status_code=500, body={"error": "fail"})
    )

    with patch(
        "services.git_integration_worker.cursor_auto.closeout_replay.fetch_turns_from",
        new_callable=AsyncMock,
        return_value=([], None),
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_replay.CursorBusClient",
        return_value=mock_client,
    ), patch(
        "services.git_integration_worker.cursor_auto.closeout_replay._post_abandon_notice",
        new_callable=AsyncMock,
    ):
        asyncio.run(startup_closeout_outbox_replay(_App("new-worker")))

    row = store.get("auto-deadbeef001")
    assert row is not None
    assert row.state == "abandoned"
