"""Progress heartbeat for long nested dispatches."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.git_integration_worker.cursor_auto.dispatch_progress import (
    ProgressEmitter,
)
from services.git_integration_worker.cursor_auto.nested_sdk import (
    dispatch_row_liveness_fresh,
    poll_dispatch_terminal,
    poll_dispatch_terminal_with_liveness,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob


def test_dispatch_row_liveness_fresh_accepts_recent_heartbeat() -> None:
    now = datetime(2026, 8, 18, 0, 0, 0, tzinfo=UTC)
    row = {
        "last_heartbeat_at": (now - timedelta(seconds=31)).isoformat(),
        "started_at": (now - timedelta(minutes=10)).isoformat(),
    }
    assert dispatch_row_liveness_fresh(row, now=now) is True


def test_dispatch_row_liveness_fresh_rejects_stale_heartbeat() -> None:
    now = datetime(2026, 8, 18, 0, 0, 0, tzinfo=UTC)
    row = {
        "last_heartbeat_at": (now - timedelta(seconds=61)).isoformat(),
        "started_at": (now - timedelta(minutes=10)).isoformat(),
    }
    assert dispatch_row_liveness_fresh(row, now=now) is False


def test_dispatch_row_liveness_fresh_uses_started_at_when_no_heartbeat() -> None:
    now = datetime(2026, 8, 18, 0, 0, 0, tzinfo=UTC)
    fresh = {"started_at": (now - timedelta(seconds=20)).isoformat()}
    stale = {"started_at": (now - timedelta(seconds=120)).isoformat()}
    assert dispatch_row_liveness_fresh(fresh, now=now) is True
    assert dispatch_row_liveness_fresh(stale, now=now) is False


def test_dispatch_row_liveness_fresh_rejects_missing_timestamps() -> None:
    now = datetime(2026, 8, 18, 0, 0, 0, tzinfo=UTC)
    assert dispatch_row_liveness_fresh(None, now=now) is False
    assert dispatch_row_liveness_fresh({}, now=now) is False


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


@pytest.mark.asyncio
async def test_poll_reenters_when_timeout_with_fresh_heartbeat() -> None:
    """9440 turn-75 shape: budget timeout + 31s-old heartbeat must not fail early."""
    now = datetime.now(UTC)
    timeout_result = {
        "ok": False,
        "terminal": False,
        "reason": "dispatch_poll_timeout",
        "last": {
            "dispatch_id": "auto-x",
            "status": "running",
            "last_heartbeat_at": (now - timedelta(seconds=31)).isoformat(),
        },
        "dispatch_id": "auto-x",
    }
    terminal_result = {
        "ok": True,
        "terminal": True,
        "status": "completed",
        "row": {"dispatch_id": "auto-x", "status": "completed"},
    }
    poll_mock = AsyncMock(side_effect=[timeout_result, terminal_result])
    with patch(
        "services.git_integration_worker.cursor_auto.nested_sdk.poll_dispatch_terminal",
        poll_mock,
    ):
        result = await poll_dispatch_terminal_with_liveness(
            thread_id="6328",
            dispatch_id="auto-x",
        )
    assert poll_mock.await_count == 2
    assert result.get("terminal") is True
    assert result.get("status") == "completed"


@pytest.mark.asyncio
async def test_poll_does_not_reenter_when_heartbeat_stale() -> None:
    now = datetime.now(UTC)
    timeout_result = {
        "ok": False,
        "terminal": False,
        "reason": "dispatch_poll_timeout",
        "last": {
            "dispatch_id": "auto-x",
            "status": "running",
            "last_heartbeat_at": (now - timedelta(seconds=61)).isoformat(),
        },
        "dispatch_id": "auto-x",
    }
    poll_mock = AsyncMock(return_value=timeout_result)
    with patch(
        "services.git_integration_worker.cursor_auto.nested_sdk.poll_dispatch_terminal",
        poll_mock,
    ):
        result = await poll_dispatch_terminal_with_liveness(
            thread_id="6328",
            dispatch_id="auto-x",
        )
    assert poll_mock.await_count == 1
    assert result.get("terminal") is False
    assert result.get("reason") == "dispatch_poll_timeout"
