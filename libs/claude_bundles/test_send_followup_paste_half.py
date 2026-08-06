"""Unit tests for send_followup_paste_half — send-only, no reply wait."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from claude_bundles.project_ask_conversation import (
    send_followup_paste_half,
    verification_marker,
)

pytestmark = pytest.mark.offline


def test_verification_marker_prefers_unique_token() -> None:
    prompt = (
        "TYPE: BREAK_IN\n"
        "#4-unique: force-not-wait-2026-08-02T10:21Z\n"
        "primary suggestion: Force now\n"
    )
    assert verification_marker(prompt) == "#4-unique: force-not-wait-2026-08-02T10:21Z"


@pytest.mark.asyncio
async def test_send_verified_true_when_marker_in_growing_transcript() -> None:
    page = AsyncMock()
    page.url = "https://claude.ai/cowork/cse_test"
    marker = "#4-unique: force-not-wait-2026-08-02T10:21Z"
    prompt = f"TYPE: BREAK_IN\n{marker}\nForce now\n"
    page.evaluate = AsyncMock(
        side_effect=[
            {"count": 1, "last_len": 10, "last_snippet": "old"},
            {"count": 2, "last_len": 80, "last_snippet": prompt[:400]},
            True,  # marker_in_committed after paste
            True,  # marker_in_committed after reload
        ]
    )
    page.reload = AsyncMock()
    with (
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new_callable=AsyncMock,
        ) as send_mock,
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new_callable=AsyncMock,
            return_value={"streaming": False, "url": page.url},
        ),
    ):
        result = await send_followup_paste_half(page, prompt)
    send_mock.assert_awaited_once()
    assert result["send_verified"] is True
    assert result["receipt"] == "dom_committed"
    assert result["error"] is None
    assert result["verification_marker"] == marker
    page.reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_dom_paste_without_reload_commit() -> None:
    """Marker in committed turns but reload drops it — stays dom_paste."""
    page = AsyncMock()
    page.url = "https://claude.ai/cowork/cse_test"
    marker = "#4-unique: force-not-wait-2026-08-02T10:21Z"
    prompt = f"TYPE: BREAK_IN\n{marker}\nForce now\n"
    page.evaluate = AsyncMock(
        side_effect=[
            {"count": 1, "last_len": 10, "last_snippet": "old"},
            {"count": 2, "last_len": 80, "last_snippet": prompt[:400]},
            True,  # marker_in_committed after paste
            False,  # marker gone after reload
        ]
    )
    page.reload = AsyncMock()
    with (
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new_callable=AsyncMock,
        ),
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new_callable=AsyncMock,
            return_value={"streaming": False, "url": page.url},
        ),
    ):
        result = await send_followup_paste_half(page, prompt)
    assert result["send_verified"] is True
    assert result["receipt"] == "dom_paste"


@pytest.mark.asyncio
async def test_send_unverified_when_count_grows_without_marker() -> None:
    """Reattach race / wrong-packet: DOM grew but unique marker absent."""
    page = AsyncMock()
    page.url = "https://claude.ai/cowork/cse_test"
    prompt = (
        "TYPE: BREAK_IN\n"
        "#4-unique: force-not-wait-2026-08-02T10:21Z\n"
        "primary suggestion: Force now\n"
    )
    page.evaluate = AsyncMock(
        side_effect=[
            {"count": 1, "last_len": 10, "last_snippet": "old"},
            {
                "count": 3,
                "last_len": 200,
                "last_snippet": "TYPE: BREAK_IN\n#3-unique: ask-ladder-…",
            },
            False,  # marker_in_committed
        ]
    )
    with (
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new_callable=AsyncMock,
        ),
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new_callable=AsyncMock,
            return_value={"streaming": False, "url": page.url},
        ),
    ):
        result = await send_followup_paste_half(page, prompt)
    assert result["send_verified"] is False
    assert result["receipt"] is None
    assert result["error"] == "send_unverified"


@pytest.mark.asyncio
async def test_composer_only_paste_does_not_verify() -> None:
    """a:27655 regression — marker only in composer, send failed."""
    page = AsyncMock()
    page.url = "https://claude.ai/cowork/cse_test"
    marker = "#4-unique: force-not-wait-2026-08-02T10:21Z"
    prompt = f"TYPE: BREAK_IN\n{marker}\nForce now\n"
    page.evaluate = AsyncMock(
        side_effect=[
            {"count": 1, "last_len": 10, "last_snippet": "old"},
            {
                "count": 1,
                "last_len": 200,
                "last_snippet": prompt[:400],
            },
            False,  # marker not in committed turns (composer-only)
        ]
    )
    with (
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new_callable=AsyncMock,
        ),
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new_callable=AsyncMock,
            return_value={"streaming": False, "url": page.url},
        ),
    ):
        result = await send_followup_paste_half(page, prompt)
    assert result["send_verified"] is False
    assert result["receipt"] is None
    assert result["error"] == "send_unverified"


@pytest.mark.asyncio
async def test_send_unverified_when_no_transcript_delta() -> None:
    page = AsyncMock()
    page.url = "https://claude.ai/cowork/cse_test"
    page.evaluate = AsyncMock(
        side_effect=[
            {"count": 2, "last_len": 20, "last_snippet": "same"},
            {"count": 2, "last_len": 20, "last_snippet": "same"},
            False,
        ]
    )
    with (
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new_callable=AsyncMock,
        ),
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new_callable=AsyncMock,
            return_value={"streaming": True, "url": page.url},
        ),
    ):
        result = await send_followup_paste_half(page, "ignored prompt text here")
    assert result["send_verified"] is False
    assert result["receipt"] is None
    assert result["error"] == "send_unverified"
    assert result["streaming_at_paste"] is True


@pytest.mark.asyncio
async def test_no_wait_assistant_reply_or_resolve_harvest_body() -> None:
    page = AsyncMock()
    page.url = "https://claude.ai/cowork/cse_test"
    page.evaluate = AsyncMock(
        side_effect=[
            {"count": 0, "last_len": 0, "last_snippet": ""},
            {"count": 1, "last_len": 17, "last_snippet": "hi there mid body"},
            True,
            True,
        ]
    )
    page.reload = AsyncMock()
    with (
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new_callable=AsyncMock,
        ),
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
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
        await send_followup_paste_half(page, "hi there mid body")
    wait_mock.assert_not_called()
    resolve_mock.assert_not_called()
