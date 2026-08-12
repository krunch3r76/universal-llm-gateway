"""Offline tests for HARVEST_JS artifact card detection (AC1)."""

from __future__ import annotations

import pytest

from claude_bundles.chat_reply_wait import HARVEST_JS

pytestmark = pytest.mark.offline

_PLAIN_CHAT_HTML = """
<!doctype html><html><body>
<main>
  <div data-testid="assistant-message">
    Here is a plain assistant reply with no artifact card attached.
    It is long enough to pass the minimum message character threshold easily.
  </div>
</main>
</body></html>
"""

_CARD_CHAT_HTML = """
<!doctype html><html><body>
<main>
  <div data-testid="assistant-message">
    <p>Bind complete. BIND: merge wins on the sidecar question.</p>
    <button>
      Bind sidecar reasoning posture merge
      Document · MD
      Google Drive
    </button>
  </div>
</main>
</body></html>
"""


async def _with_page(html: str):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content(html)
    return pw, browser, page


@pytest.mark.asyncio
async def test_harvest_js_detects_document_md_card() -> None:
    pw, browser, page = await _with_page(_CARD_CHAT_HTML)
    try:
        state = await page.evaluate(HARVEST_JS, {"minMsgChars": 10})
        cards = state.get("artifact_cards") or []
        assert len(cards) == 1
        assert cards[0]["title"] == "Bind sidecar reasoning posture merge"
        assert cards[0]["kind"] == "MD"
    finally:
        await browser.close()
        await pw.stop()


@pytest.mark.asyncio
async def test_harvest_js_plain_chat_has_empty_artifact_cards() -> None:
    pw, browser, page = await _with_page(_PLAIN_CHAT_HTML)
    try:
        state = await page.evaluate(HARVEST_JS, {"minMsgChars": 10})
        assert state.get("artifact_cards") == []
    finally:
        await browser.close()
        await pw.stop()
