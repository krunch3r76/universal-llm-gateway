"""Unit tests for cursor-sdk generate admit-only coord-thread pointer."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from .admit_provenance import AdmitProvenanceContext
from .cursor_sdk_admit_loop import (
    admit_pointer_would_have_refused_total,
    reset_admit_pointer_would_have_refused_counter_for_tests,
)
from .cursor_sdk_coord_notify import post_coord_admit_pointer
from .handoff import post_pointer_turn


@pytest.fixture(autouse=True)
def _reset_counter() -> None:
    reset_admit_pointer_would_have_refused_counter_for_tests()


@pytest.mark.asyncio
async def test_coord_admit_skips_when_same_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
        lambda *a, **k: _ClientCtx(post_turn),
    )
    await post_coord_admit_pointer(
        coord_thread_id="1959",
        worker_thread_id="1959",
        to_agent="claude-web",
        caller_agent="claude-web",
        contract="implement",
    )
    post_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_coord_admit_posts_when_threads_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
        lambda *a, **k: _ClientCtx(post_turn),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    await post_coord_admit_pointer(
        coord_thread_id="1959",
        worker_thread_id="1960",
        to_agent="claude-web",
        caller_agent="claude-web",
        contract="implement",
    )
    post_turn.assert_awaited_once()
    payload = post_turn.await_args.args[1]
    assert payload["thread"] == "1959"
    assert payload["subject"] == "cursor-sdk generate admitted"
    assert "poll_hint" in payload["body"]
    assert "1960" in payload["body"]
    assert "bound todo" in payload["body"]


@pytest.mark.asyncio
async def test_coord_admit_non_implement_omits_todo_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
        lambda *a, **k: _ClientCtx(post_turn),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    await post_coord_admit_pointer(
        coord_thread_id="1959",
        worker_thread_id="1960",
        to_agent="claude-web",
        caller_agent="claude-web",
        contract="none",
    )
    payload = post_turn.await_args.args[1]
    assert "bound todo" not in payload["body"]


@pytest.mark.asyncio
async def test_a6655_loop_closure_refuses_and_does_not_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B.3 — loop detector fires, admit does not post."""
    post_turn = AsyncMock()
    published: list[str] = []

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
        lambda *a, **k: _ClientCtx(post_turn),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")

    def _capture(event: object) -> None:
        published.append(getattr(event, "signal", ""))

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.publish_frontier_event",
        _capture,
    )

    await post_coord_admit_pointer(
        coord_thread_id="7031",
        worker_thread_id="7032",
        to_agent="dispatch",
        caller_agent="cursor-auto",
        contract="implement",
        request_id="req-6655",
        execution_id="exec-6655",
        prompt_source_thread="7031",
        prompt_bind_mode="latest",
        prompt_turn_number=None,
        has_explicit_prompt_source=False,
    )
    post_turn.assert_not_awaited()
    assert "frontier.admit_pointer.loop_closure" in published
    assert admit_pointer_would_have_refused_total() == 1


_WORK_KEY = "decision:maestro-seat-cost-effectiveness"


@pytest.mark.asyncio
async def test_coord_admit_and_worker_turn1_stamp_work_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
        lambda *a, **k: _ClientCtx(post_turn),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.handoff.make_async_client",
        lambda *a, **k: _ClientCtx(post_turn),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    ctx = AdmitProvenanceContext(
        missing=(),
        caller_seat="cursor-auto",
        lane_or_thread="16416",
        work_key=_WORK_KEY,
        coord_thread="16416",
        root_lane=True,
        request_id="req-l3",
    )
    await post_coord_admit_pointer(
        coord_thread_id="16416",
        worker_thread_id="18418",
        to_agent="dispatch",
        caller_agent="cursor-auto",
        contract="implement",
        admit_provenance=ctx,
    )
    coord_payload = post_turn.await_args_list[0].args[1]
    assert f"work_key: {_WORK_KEY}" in coord_payload["body"]
    assert "PROVENANCE_MISSING:work_key" not in coord_payload["body"]

    post_turn.reset_mock()
    await post_pointer_turn(
        request_id="req-l3",
        thread_id="18418",
        to_agent="cursor-sdk:dispatch:exec",
        subject="cursor-sdk generate — exec",
        pointer_body="Worker thread opened.",
        caller_agent="cursor-auto",
        provenance_missing=(),
        work_key=_WORK_KEY,
    )
    worker_payload = post_turn.await_args.args[1]
    assert f"work_key: {_WORK_KEY}" in worker_payload["body"]
    assert "PROVENANCE_MISSING:work_key" not in worker_payload["body"]


@pytest.mark.asyncio
async def test_a6655_frozen_same_thread_silent_no_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    published: list[str] = []

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
        lambda *a, **k: _ClientCtx(post_turn),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.publish_frontier_event",
        lambda event: published.append(getattr(event, "signal", "")),
    )

    await post_coord_admit_pointer(
        coord_thread_id="7031",
        worker_thread_id="7032",
        to_agent="dispatch",
        caller_agent="cursor-auto",
        contract="implement",
        prompt_source_thread="7031",
        prompt_bind_mode="frozen_turn",
        prompt_turn_number=12,
        has_explicit_prompt_source=False,
    )
    post_turn.assert_awaited_once()
    assert published == []
    assert admit_pointer_would_have_refused_total() == 0


class _ClientCtx:
    def __init__(self, post_turn: AsyncMock) -> None:
        self._post_turn = post_turn

    async def __aenter__(self) -> _ClientCtx:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, path: str, **kwargs: object) -> object:
        if path == "/turns":
            await self._post_turn(path, kwargs.get("json"))
        return type(
            "Resp", (), {"status_code": 200, "json": lambda self: {"turn_number": 1}}
        )()
