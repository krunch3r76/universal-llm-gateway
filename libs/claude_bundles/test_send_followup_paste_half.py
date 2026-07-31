"""Unit tests for send_followup_paste_half — send-only, no reply wait."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from claude_bundles.project_ask_conversation import send_followup_paste_half

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_send_verified_true_when_transcript_grows() -> None:
    page = AsyncMock()
    page.url = "https://claude.ai/cowork/cse_test"
    page.evaluate = AsyncMock(
        side_effect=[
            {"count": 1, "last_len": 10, "last_snippet": "old"},
            {"count": 2, "last_len": 20, "last_snippet": "hello world"},
        ]
    )
    with (
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new_callable=AsyncMock,
        ) as send_mock,
        patch(
            "claude_bundles.chat_reply_wait.harvest_assistant",
            new_callable=AsyncMock,
            return_value={"streaming": False, "url": page.url},
        ),
    ):
        result = await send_followup_paste_half(page, "hello world")
    send_mock.assert_awaited_once()
    assert result["send_verified"] is True
    assert result["error"] is None


@pytest.mark.asyncio
async def test_send_unverified_when_no_transcript_delta() -> None:
    page = AsyncMock()
    page.url = "https://claude.ai/cowork/cse_test"
    page.evaluate = AsyncMock(
        side_effect=[
            {"count": 2, "last_len": 20, "last_snippet": "same"},
            {"count": 2, "last_len": 20, "last_snippet": "same"},
        ]
    )
    with (
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new_callable=AsyncMock,
        ),
        patch(
            "claude_bundles.chat_reply_wait.harvest_assistant",
            new_callable=AsyncMock,
            return_value={"streaming": True, "url": page.url},
        ),
    ):
        result = await send_followup_paste_half(page, "ignored")
    assert result["send_verified"] is False
    assert result["error"] == "send_unverified"
    assert result["streaming_at_paste"] is True


@pytest.mark.asyncio
async def test_no_wait_assistant_reply_or_resolve_harvest_body() -> None:
    page = AsyncMock()
    page.url = "https://claude.ai/cowork/cse_test"
    page.evaluate = AsyncMock(
        side_effect=[
            {"count": 0, "last_len": 0, "last_snippet": ""},
            {"count": 1, "last_len": 5, "last_snippet": "hi"},
        ]
    )
    with (
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new_callable=AsyncMock,
        ),
        patch(
            "claude_bundles.chat_reply_wait.harvest_assistant",
            new_callable=AsyncMock,
            return_value={"streaming": False, "url": page.url},
        ),
        patch(
            "claude_bundles.chat_reply_wait.wait_assistant_reply",
            new_callable=AsyncMock,
        ) as wait_mock,
        patch(
            "claude_bundles.cowork_output_download.resolve_harvest_body",
            new_callable=AsyncMock,
        ) as resolve_mock,
    ):
        await send_followup_paste_half(page, "hi")
    wait_mock.assert_not_called()
    resolve_mock.assert_not_called()
