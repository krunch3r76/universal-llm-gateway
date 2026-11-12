"""Await-reply bus poll hot path (cpu-pin C)."""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_sdk_await_reply import (
    AwaitedGenerate,
    _snapshot_replies,
)


@pytest.mark.asyncio
async def test_snapshot_replies_dedupes_thread_after_turn_per_tick() -> None:
    calls: list[tuple[str, int]] = []

    def turns_fn(thread_id: str, after_turn: int) -> list[dict]:
        calls.append((thread_id, after_turn))
        return []

    awaited = [
        AwaitedGenerate(
            execution_id="e1",
            thread_id="22613",
            after_turn=3,
            from_agent="web",
            model=None,
            fired_at="",
        ),
        AwaitedGenerate(
            execution_id="e2",
            thread_id="22613",
            after_turn=3,
            from_agent="web",
            model=None,
            fired_at="",
        ),
    ]
    await _snapshot_replies(awaited, bus_turns_fn=turns_fn)
    assert calls == [("22613", 3)]


