"""HARVEST_JS restores KaTeX annotation as dollar-delimited source (a:37508)."""

from __future__ import annotations

import pytest

from claude_bundles.chat_reply_wait import HARVEST_JS

pytestmark = pytest.mark.offline

_KATEX_CHAT_HTML = """
<!doctype html><html><body>
<main>
  <div data-testid="assistant-message">
    <p>not </p>
    <span class="katex">
      <span class="katex-mathml">
        <math>
          <annotation encoding="application/x-tex">ULG_REPO: HOME=&quot;</annotation>
        </math>
      </span>
      <span class="katex-html" aria-hidden="true">
        <span class="mord mathnormal">U</span>
        <span class="mord mathnormal">L</span>
        <span class="mord mathnormal">G</span>
      </span>
    </span>
    <p>$(getent passwd) install-ecosystem-plugin.sh</p>
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
async def test_harvest_js_restores_katex_tex_not_glyph_innertext() -> None:
    pw, browser, page = await _with_page(_KATEX_CHAT_HTML)
    try:
        state = await page.evaluate(HARVEST_JS, {"minMsgChars": 10})
        body = state.get("body") or ""
        assert "$ULG_REPO: HOME=\"" in body
        assert "install-ecosystem-plugin.sh" in body
        assert "\nU\n" not in body
    finally:
        await browser.close()
        await pw.stop()
