"""Unit tests for send_followup_paste_half — send-only, no reply wait."""

from __future__ import annotations

from contextlib import contextmanager
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


@contextmanager
def _paste_env(*, holds: bool = False, survives: bool = True, streaming: bool = False):
    with (
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new_callable=AsyncMock,
        ) as send_mock,
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new_callable=AsyncMock,
            return_value={
                "streaming": streaming,
                "url": "https://claude.ai/cowork/cse_test",
            },
        ),
        patch(
            "claude_bundles.project_ask_conversation.composer_holds_draft",
            new_callable=AsyncMock,
            return_value=holds,
        ),
        patch(
            "claude_bundles.project_ask_conversation.marker_survives_settle",
            new_callable=AsyncMock,
            return_value=survives,
        ),
    ):
        yield send_mock



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
            True,
        ]
    )
    page.reload = AsyncMock()
    with _paste_env(survives=True) as send_mock:
        result = await send_followup_paste_half(page, prompt)
    send_mock.assert_awaited_once()
    assert result["send_verified"] is True
    assert result["receipt"] == "dom_committed"
    assert result["error"] is None
    assert result["verification_marker"] == marker
    page.reload.assert_not_called()


@pytest.mark.asyncio
async def test_dom_paste_when_marker_does_not_survive_settle() -> None:
    """Marker in committed turns but settle drops it — stays dom_paste, no reload."""
    page = AsyncMock()
    page.url = "https://claude.ai/cowork/cse_test"
    marker = "#4-unique: force-not-wait-2026-08-02T10:21Z"
    prompt = f"TYPE: BREAK_IN\n{marker}\nForce now\n"
    page.evaluate = AsyncMock(
        side_effect=[
            {"count": 1, "last_len": 10, "last_snippet": "old"},
            {"count": 2, "last_len": 80, "last_snippet": prompt[:400]},
            True,
        ]
    )
    page.reload = AsyncMock()
    with _paste_env(survives=False):
        result = await send_followup_paste_half(page, prompt)
    assert result["send_verified"] is True
    assert result["receipt"] == "dom_paste"
    page.reload.assert_not_called()


@pytest.mark.asyncio
async def test_composer_holding_draft_is_unverified_no_reload() -> None:
    page = AsyncMock()
    page.url = "https://claude.ai/cowork/cse_test"
    marker = "#4-unique: force-not-wait-2026-08-02T10:21Z"
    prompt = f"TYPE: BREAK_IN\n{marker}\nForce now\n"
    page.evaluate = AsyncMock(
        side_effect=[
            {"count": 1, "last_len": 10, "last_snippet": "old"},
            {"count": 2, "last_len": 80, "last_snippet": prompt[:400]},
        ]
    )
    page.reload = AsyncMock()
    with _paste_env(holds=True):
        result = await send_followup_paste_half(page, prompt)
    assert result["send_verified"] is False
    assert result["receipt"] is None
    assert result["error"] == "send_unverified"
    assert result["detail"] == "composer still holds draft"
    page.reload.assert_not_called()


@pytest.mark.asyncio
async def test_send_prompt_error_is_unverified_no_reload() -> None:
    page = AsyncMock()
    page.url = "https://claude.ai/cowork/cse_test"
    page.evaluate = AsyncMock(
        return_value={"count": 1, "last_len": 10, "last_snippet": "old"}
    )
    page.reload = AsyncMock()
    with (
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new_callable=AsyncMock,
            side_effect=RuntimeError("submit did not clear composer"),
        ),
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new_callable=AsyncMock,
            return_value={"streaming": False, "url": page.url},
        ),
    ):
        result = await send_followup_paste_half(page, "#1-unique: x\nbody")
    assert result["send_verified"] is False
    assert result["receipt"] is None
    assert "did not clear composer" in (result.get("detail") or "")
    page.reload.assert_not_called()


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
            False,
        ]
    )
    with _paste_env():
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
            False,
        ]
    )
    with _paste_env(holds=True):
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
    with _paste_env(streaming=True):
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
        ]
    )
    page.reload = AsyncMock()
    with (
        _paste_env(survives=True),
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
    page.reload.assert_not_called()
