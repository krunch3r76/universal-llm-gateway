"""``dispatch.job.superseded.notify`` — event, lane delivery, poll termination."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from agent_bus_store.wait_status import derive_status, is_complete

from services.git_integration_worker.cursor_auto.queue import AutoJobQueue
from services.git_integration_worker.cursor_auto.supersede import (
    QUEUE_WITHDRAW,
    SUPERSEDED_TERMINAL,
    supersede_same_thread_inflight,
)
from services.git_integration_worker.cursor_auto.dispatch_job_superseded_events import (
    emit_dispatch_job_superseded_notify,
)


def _enqueue(queue: AutoJobQueue, *, thread_id: str, turn_number: int):
    return queue.enqueue(
        thread_id=thread_id,
        turn_number=turn_number,
        subject=f"turn {turn_number}",
        body="body",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
        cse_registration_id="reg-loser",
        cse_chat_url="https://claude.ai/chat/loser",
    )


def test_emit_dispatch_job_superseded_notify_payload():
    emitted: list[dict] = []

    with patch(
        "services.git_integration_worker.cursor_auto.dispatch_job_superseded_events."
        "emit_frontier_event",
        side_effect=lambda evt: emitted.append(evt.payload),
    ):
        emit_dispatch_job_superseded_notify(
            superseded_job_id="job-old",
            superseding_job_id="job-new",
            method="queue_withdraw",
            reason="same_thread_request_turn_9",
            thread_id="7188",
            superseded_dispatch_id=None,
        )
    assert emitted == [
        {
            "superseded_job_id": "job-old",
            "superseding_job_id": "job-new",
            "method": "queue_withdraw",
            "reason": "same_thread_request_turn_9",
            "thread_id": "7188",
        }
    ]


def test_supersede_emits_notify_and_wait_completes_on_status_superseded():
    """Integration: real supersede path posts terminal + poll completes."""
    queue = AutoJobQueue(durable=False)
    old = _enqueue(queue, thread_id="7188", turn_number=8)
    new = _enqueue(queue, thread_id="7188", turn_number=9)

    bus = AsyncMock()
    terminal_subject = f"{SUPERSEDED_TERMINAL} — turn 8"
    bus.reply = AsyncMock(
        return_value=MagicMock(status_code=200, body={"turn_number": 2})
    )

    with (
        patch(
            "services.git_integration_worker.cursor_auto.superseded_seat_notify."
            "emit_dispatch_job_superseded_notify",
        ) as emit_mock,
        patch(
            "services.git_integration_worker.cursor_auto.superseded_seat_notify."
            "deliver_cse_wake",
            return_value={"ok": True, "send_verified": True},
        ),
    ):
        asyncio.run(supersede_same_thread_inflight(new, queue=queue, client=bus))

    emit_mock.assert_called_once()
    emit_kwargs = emit_mock.call_args.kwargs
    assert emit_kwargs["superseded_job_id"] == old.job_id
    assert emit_kwargs["superseding_job_id"] == new.job_id
    assert emit_kwargs["method"] == QUEUE_WITHDRAW
    assert emit_kwargs["thread_id"] == "7188"

    assert queue.is_superseded(old.job_id)
    bus.reply.assert_awaited_once()
    assert bus.reply.await_args.kwargs["subject"].startswith(SUPERSEDED_TERMINAL)

    turns = [
        {
            "turn_number": 1,
            "from_agent": "web-anthropic",
            "subject": "request",
            "body": "",
            "read_at": None,
            "status": "open",
        },
        {
            "turn_number": 2,
            "from_agent": "cursor-auto",
            "subject": terminal_subject,
            "body": json.dumps({"terminal_vocabulary": SUPERSEDED_TERMINAL}),
            "read_at": None,
            "status": "open",
        },
    ]
    thread = {"status": "active"}
    comp = {"mode": "status:superseded"}
    wait_complete = is_complete(thread, turns, after_turn=1, completion=comp)
    wait_status = derive_status(thread, turns, after_turn=1, completion=comp)
    assert wait_complete is True
    assert wait_status == "complete"
    terminating_poll = {
        "status": wait_status,
        "completion": comp,
        "complete": wait_complete,
        "turn": turns[1],
    }
    assert terminating_poll == {
        "status": "complete",
        "completion": {"mode": "status:superseded"},
        "complete": True,
        "turn": turns[1],
    }


def test_pre_register_live_run_posts_terminal_immediately():
    queue = AutoJobQueue(durable=False)
    old = _enqueue(queue, thread_id="7188", turn_number=8)
    queue.claim_next()
    new = _enqueue(queue, thread_id="7188", turn_number=9)

    bus = AsyncMock()
    bus.reply = AsyncMock(
        return_value=MagicMock(status_code=200, body={"turn_number": 2})
    )

    with patch(
        "services.git_integration_worker.cursor_auto.supersede.live_run_for_thread",
        return_value=None,
    ):
        with patch(
            "services.git_integration_worker.cursor_auto.superseded_seat_notify."
            "deliver_cse_wake",
            return_value={"ok": True},
        ):
            asyncio.run(supersede_same_thread_inflight(new, queue=queue, client=bus))

    bus.reply.assert_awaited_once()
    assert bus.reply.await_args.kwargs["subject"].startswith(SUPERSEDED_TERMINAL)
