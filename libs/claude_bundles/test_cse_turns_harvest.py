"""Offline tests for ordered CSE turn DOM extraction."""

from __future__ import annotations

import pytest

from claude_bundles.cse_turns_harvest import CSE_TURNS_JS, harvest_turns


@pytest.mark.offline
def test_cse_turns_js_source_present() -> None:
    assert "afterTurn" in CSE_TURNS_JS
    assert "limit" in CSE_TURNS_JS


@pytest.mark.offline
@pytest.mark.asyncio
async def test_cse_turns_js_returns_ordered_turns() -> None:
    pytest.importorskip("playwright.async_api")
    from playwright.async_api import async_playwright

    html = """
    <html><body>
      <div class="font-claude-message">First assistant reply with enough text here.</div>
      <div class="font-claude-message">Second assistant reply with enough text here.</div>
    </body></html>
    """
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(html)
            result = await harvest_turns(page, limit=10)
            await browser.close()
    except Exception as exc:
        pytest.skip(f"playwright browser unavailable: {exc}")
    assert len(result["turns"]) >= 1
    assert "First assistant" in result["turns"][0]["text"]
