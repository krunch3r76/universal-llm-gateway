"""Progress heartbeat for long nested dispatches."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.git_integration_worker.cursor_auto.dispatch_progress import (
    ProgressEmitter,
)
from services.git_integration_worker.cursor_auto.nested_sdk import poll_dispatch_terminal
from services.git_integration_worker.cursor_auto.queue import AutoJob


@pytest.mark.asyncio
async def test_progress_emitter_posts_status_progress() -> None:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    job = AutoJob(
        job_id="j-prog",
        thread_id="6328",
        turn_number=1,
        subject="long nest",
        body="",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="low",
        contract="implement",
    )
    emitter = ProgressEmitter(job, client=client, interval_s=0.0)
    await emitter.maybe_emit({"status": "running", "dispatch_id": "auto-abc"})
    subject = client.reply.await_args.kwargs["subject"]
    body = client.reply.await_args.kwargs["body"]
    assert "progress" in subject
    assert "status:done" not in subject
    assert "status:done" not in body


@pytest.mark.asyncio
async def test_poll_on_tick_does_not_complete_done_waiter_token() -> None:
    emitted: list[str] = []

    async def on_tick(row: dict | None) -> None:
        emitted.append("tick")

    rows = [{"dispatch_id": "auto-x", "status": "running"}]

    def fake_status(*, thread_id: str) -> dict:
        return rows[0]

    async def invoke_to_thread(fn, /, *args, **kwargs):
        return fn(*args, **kwargs)

    with patch(
        "services.git_integration_worker.cursor_auto.nested_sdk.CursorDispatchLedger"
    ) as ledger_cls:
        ledger = ledger_cls.instance.return_value
        ledger.dispatch_status_by_thread = fake_status
        with (
            patch(
                "services.git_integration_worker.cursor_auto.nested_sdk.asyncio.sleep",
                new=AsyncMock(),
            ),
            patch(
                "services.git_integration_worker.cursor_auto.nested_sdk.asyncio.to_thread",
                side_effect=invoke_to_thread,
            ),
        ):
            result = await poll_dispatch_terminal(
                thread_id="6328",
                dispatch_id="auto-x",
                timeout_s=0.01,
                on_tick=on_tick,
            )
    assert emitted
    assert result.get("terminal") is False
