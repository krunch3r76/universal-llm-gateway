"""Tests for Lane-B mint lane↔branch association producer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from services.git_integration_worker.cursor_sdk_lane_branch import associate_lane_branch


class _FakeResponse:
    def __init__(self, *, status_code: int) -> None:
        self.status_code = status_code


@pytest.mark.asyncio
async def test_associate_lane_branch_posts_expected_path_and_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = AsyncMock(return_value=_FakeResponse(status_code=200))
    client = AsyncMock()
    client.post = post

    class _Ctx:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *args):
            return None

    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_lane_branch.make_async_client",
        lambda *_a, **_k: _Ctx(),
    )

    ok = await associate_lane_branch(thread_id="7119", branch_name="cursor-sdk/abc")

    assert ok is True
    post.assert_awaited_once_with(
        "/threads/7119/branch-associate",
        json={"branch_name": "cursor-sdk/abc"},
        headers={"Authorization": "Bearer test-token"},
    )


@pytest.mark.asyncio
async def test_associate_lane_branch_transport_error_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("down"))

    class _Ctx:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_lane_branch.make_async_client",
        lambda *_a, **_k: _Ctx(),
    )

    ok = await associate_lane_branch(thread_id="7119", branch_name="cursor-sdk/abc")

    assert ok is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("thread_id", "branch_name"),
    [
        ("", "cursor-sdk/abc"),
        ("7119", ""),
        ("  ", "cursor-sdk/abc"),
        ("7119", "  "),
    ],
)
async def test_associate_lane_branch_blank_args_skip_request(
    monkeypatch: pytest.MonkeyPatch,
    thread_id: str,
    branch_name: str,
) -> None:
    factory = MagicMock()
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_lane_branch.make_async_client",
        factory,
    )

    ok = await associate_lane_branch(thread_id=thread_id, branch_name=branch_name)

    assert ok is False
    factory.assert_not_called()
