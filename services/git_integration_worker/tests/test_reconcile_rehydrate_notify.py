"""Tests for rehydrate-class queue_owner_restart notify wording (mission 9440 S-2iii)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.git_integration_worker.cursor_auto.closeout_outbox import (
    CloseoutOutboxStore,
)
from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_queue_owner_restart_terminal,
)
from services.git_integration_worker.cursor_auto.job_ledger import (
    AutoJobLedger,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.job_reconcile import (
    _REHYDRATE_GENERATION_CAP,
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


def _enqueue(*, thread_id: str = "9440-notify", turn: int = 1):
    return get_queue().enqueue(
        thread_id=thread_id,
        turn_number=turn,
        subject=f"turn {turn}",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )


def test_rehydrate_exhausted_notify_names_generation_not_reissue_only() -> None:
    job = _enqueue()
    get_ledger().merge_record_json(
        job.job_id, {"generation": _REHYDRATE_GENERATION_CAP}
    )
    reset_queue_for_tests(durable=True)

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    with patch(
        "services.git_integration_worker.cursor_auto.job_reconcile.CursorBusClient",
        return_value=bus,
    ):
        terminalized = asyncio.run(
            reconcile_open_auto_jobs(post_bus=True, rehydrate=True)
        )

    assert len(terminalized) == 1
    body = json.loads(bus.reply.await_args.kwargs["body"])
    assert str(_REHYDRATE_GENERATION_CAP) in body["summary"]
    assert body["generation"] == _REHYDRATE_GENERATION_CAP
    assert body["rehydrate_exhausted"] is True


def test_rehydrate_recovered_notify_never_says_reissue() -> None:
    _enqueue()
    reset_queue_for_tests(durable=True)

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    with patch(
        "services.git_integration_worker.cursor_auto.job_reconcile.CursorBusClient",
        return_value=bus,
    ):
        asyncio.run(reconcile_open_auto_jobs(post_bus=True, rehydrate=True))

    assert bus.reply.await_count >= 1
    call = bus.reply.await_args
    assert not call.kwargs["subject"].startswith("status:")
    body = json.loads(call.kwargs["body"])
    assert "re-issue" not in body["summary"].lower()
    assert body["rehydrated"] is True
    assert body["generation"] == 1


def test_ordinary_claimed_lost_notify_wording_unchanged() -> None:
    job = _enqueue()
    get_queue().claim_next()

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    asyncio.run(
        post_queue_owner_restart_terminal(job, client=bus, queue=get_queue())
    )

    body = json.loads(bus.reply.await_args.kwargs["body"])
    assert body["summary"] == (
        "Auto job lost when git_integration_worker restarted "
        "(dead_on_giw_restart); re-issue the DIRECTIVE."
    )
    assert "rehydrate_exhausted" not in body
