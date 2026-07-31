"""Executable HARVEST_JS error_banner scan-scope falsifiers (25486)."""

from __future__ import annotations

import pytest

from claude_bundles.chat_reply_wait import HARVEST_JS

pytest.importorskip("playwright")
from playwright.async_api import async_playwright  # noqa: E402

pytestmark = pytest.mark.offline

_COMPOSER_RATE_LIMIT_HTML = """
<!doctype html><html><body>
<main>
  <div data-testid="assistant-message">Completed assistant reply with enough text for harvest.</div>
  <div data-testid="chat-input" contenteditable="true">
    Please summarize the rate limit policy for our API usage.
  </div>
</main>
</body></html>
"""

_TOAST_RATE_LIMIT_HTML = """
<!doctype html><html><body>
<main>
  <div data-testid="assistant-message">Completed assistant reply with enough text for harvest.</div>
  <div data-testid="chat-input" contenteditable="true"></div>
</main>
<div role="alert">You hit a rate limit. Try again later.</div>
</body></html>
"""


async def _harvest(html: str) -> dict:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(html)
            return await page.evaluate(HARVEST_JS, {"minMsgChars": 10})
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_harvest_error_banner_false_when_phrase_only_in_composer() -> None:
    """Neg falsifier: composer-only rate limit text must not set error_banner."""
    state = await _harvest(_COMPOSER_RATE_LIMIT_HTML)
    assert state["error_banner"] is False
    assert state["error_banner_match"] == ""
    assert state["error_banner_text"] == ""


@pytest.mark.asyncio
async def test_harvest_error_banner_true_when_phrase_in_alert() -> None:
    """Pos falsifier: alert/toast rate limit must set error_banner + match."""
    state = await _harvest(_TOAST_RATE_LIMIT_HTML)
    assert state["error_banner"] is True
    assert "rate limit" in state["error_banner_match"].lower()
    assert state["error_banner_text"]
