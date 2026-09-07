"""Hermetic tests for composer submit proof (draft-clear, not click self-report)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("playwright")
from playwright.async_api import async_playwright  # noqa: E402

from claude_bundles.composer_submit import (
    composer_holds_needle,
    press_send_chords,
    prove_composer_submitted,
    submit_composer_content,
    verification_marker,
)

pytestmark = pytest.mark.offline

_STREAMING_NO_SEND_HTML = """
<!doctype html><html><head><title>Chat - Claude</title></head><body>
<main>
  <div data-is-streaming="true"></div>
  <div data-testid="chat-input" contenteditable="true" style="min-height:80px;width:400px"></div>
</main>
<script>
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.shiftKey) return;
    const el = document.querySelector('[data-testid="chat-input"]');
    if (el) { el.innerText = ''; el.textContent = ''; }
  });
</script>
</body></html>
"""


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
    assert "Enter" in pressed
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


@pytest.mark.asyncio
async def test_submit_composer_content_enter_when_no_send_button() -> None:
    prompt = "#9-unique: stream-break-in\nCONFER doorbell"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_STREAMING_NO_SEND_HTML)
        composer = page.locator('[data-testid="chat-input"]')
        await composer.click()
        await page.keyboard.insert_text(prompt)
        await submit_composer_content(page, prompt, composer=composer)
        draft = await page.evaluate(
            """() => {
              const el = document.querySelector('[data-testid="chat-input"]');
              return (el && (el.innerText || el.textContent || '')).trim();
            }"""
        )
        assert draft == ""
        await browser.close()
