"""Hermetic tests for composer submit proof (draft-clear, not click self-report)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from claude_bundles.composer_submit import (
    composer_holds_needle,
    press_send_chords,
    prove_composer_submitted,
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


def test_composer_holds_needle_detects_draft() -> None:
    needle = "#4-unique: force-not-wait-2026-08-02T10:21Z"
    assert composer_holds_needle({"text": f"hello {needle}"}, needle)
    assert not composer_holds_needle({"text": "empty composer"}, needle)
    assert not composer_holds_needle({"text": needle}, "")


@pytest.mark.asyncio
async def test_prove_returns_when_composer_already_clear() -> None:
    page = AsyncMock()
    page.evaluate = AsyncMock(return_value={"ok": True, "text": "", "len": 0})
    await prove_composer_submitted(page, "#1-unique: already-sent\nbody")
    page.keyboard.press.assert_not_called()


@pytest.mark.asyncio
async def test_prove_chords_then_raises_when_draft_stays() -> None:
    page = AsyncMock()
    needle = "#1-unique: stuck-draft"
    page.evaluate = AsyncMock(
        return_value={"ok": True, "text": needle, "len": len(needle)}
    )
    page.wait_for_timeout = AsyncMock()
    page.keyboard.press = AsyncMock()
    with pytest.raises(RuntimeError, match="did not clear composer"):
        await prove_composer_submitted(page, f"{needle}\nbody")
    pressed = [c.args[0] for c in page.keyboard.press.await_args_list]
    assert "Control+Enter" in pressed
    assert "Meta+Enter" in pressed


@pytest.mark.asyncio
async def test_press_send_chords_skips_meta_when_control_clears() -> None:
    page = AsyncMock()
    needle = "#1-unique: cleared-by-ctrl"
    page.evaluate = AsyncMock(return_value={"ok": True, "text": "", "len": 0})
    page.wait_for_timeout = AsyncMock()
    page.keyboard.press = AsyncMock()
    await press_send_chords(page, needle)
    page.keyboard.press.assert_awaited_once_with("Control+Enter")
