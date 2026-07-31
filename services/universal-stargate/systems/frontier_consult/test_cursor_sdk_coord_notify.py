"""Unit tests for cursor-sdk generate admit-only coord-thread pointer."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from .cursor_sdk_coord_notify import post_coord_admit_pointer


@pytest.mark.asyncio
async def test_coord_admit_skips_when_same_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_turn = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_coord_notify.make_async_client",
        lambda *a, **k: _client_ctx(post_turn),
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
        lambda *a, **k: _client_ctx(post_turn),
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
        lambda *a, **k: _client_ctx(post_turn),
    )
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    await post_coord_admit_pointer(
        coord_thread_id="1959",
        worker_thread_id="1960",
        to_agent="claude-web",
        caller_agent="claude-web",
        contract="light-bounded",
    )
    payload = post_turn.await_args.args[1]
    assert "bound todo" not in payload["body"]


class _client_ctx:
    def __init__(self, post_turn: AsyncMock) -> None:
        self._post_turn = post_turn

    async def __aenter__(self) -> _client_ctx:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, path: str, **kwargs: object) -> object:
        if path == "/turns":
            await self._post_turn(path, kwargs.get("json"))
        return type("Resp", (), {"status_code": 200})()
