"""Regression: default CursorBusClient() in supersede must stay hermetic."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto import supersede as auto_supersede
from services.git_integration_worker.cursor_auto.queue import AutoJobQueue
from services.git_integration_worker.cursor_auto.supersede import (
    supersede_same_thread_inflight,
)


def _enqueue(queue: AutoJobQueue, *, thread_id: str, turn_number: int):
    return queue.enqueue(
        thread_id=thread_id,
        turn_number=turn_number,
        subject=f"turn {turn_number}",
        body="TYPE: DIRECTIVE\n## Scope\nx\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )


def test_supersede_default_client_does_not_open_live_transport() -> None:
    """Falsifier for live bus posts when tests omit client= (arc 12286)."""
    live_hits: list[str] = []

    class _Ctx:
        async def __aenter__(self):
            live_hits.append("enter")
            raise AssertionError("live make_async_client must not run")

        async def __aexit__(self, *_args: object) -> None:
            return None

    with patch(
        "services.git_integration_worker.cursor_bus.make_async_client",
        lambda *_a, **_k: _Ctx(),
    ):
        queue = AutoJobQueue(durable=False)
        old = _enqueue(queue, thread_id="9004", turn_number=1)
        queue.claim_next()
        new = _enqueue(queue, thread_id="9004", turn_number=2)
        evidence = asyncio.run(
            supersede_same_thread_inflight(new, queue=queue)
        )
        auto_supersede._PENDING.clear()

    assert evidence is not None
    assert evidence["method"] == auto_supersede.PRE_REGISTER_LIVE_RUN
    assert live_hits == []
