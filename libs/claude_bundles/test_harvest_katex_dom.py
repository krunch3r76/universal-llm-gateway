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


_KATEX_SPAN = (
    '<span class="katex"><span class="katex-mathml"><math>'
    '<annotation encoding="application/x-tex">ULG_REPO: HOME=&quot;</annotation>'
    "</math></span>"
    '<span class="katex-html" aria-hidden="true">'
    '<span class="mord mathnormal">U</span>'
    '<span class="mord mathnormal">L</span>'
    '<span class="mord mathnormal">G</span>'
    "</span></span>"
)

# No whitespace text nodes between elements (a:37508 B1).
_COMPACT_CHAT_HTML = (
    "<!doctype html><html><body><main>"
    '<div data-testid="assistant-message">'
    "<div>Claude responded: VERDICT: Change</div>"
    "<button>Used toys integration, loaded tools, loaded a skill</button>"
    "<p>VERDICT: Change</p>"
    "<p>Run it from</p><br><p>here, not </p>"
    f"{_KATEX_SPAN}"
    "<p>$(getent passwd)</p>"
    "<pre>def f():\n    return 1\n}</pre>"
    "<div>3 minutes ago</div>"
    "</div></main></body></html>"
)


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


@pytest.mark.asyncio
async def test_harvest_js_compact_dom_keeps_innertext_lines_except_math() -> None:
    from claude_bundles.project_ask import finalize_scrape_body

    pw, browser, page = await _with_page(_COMPACT_CHAT_HTML)
    try:
        live = await page.evaluate(
            """() => (document.querySelector("[data-testid='assistant-message']")
              .innerText || '').trim()"""
        )
        state = await page.evaluate(HARVEST_JS, {"minMsgChars": 10})
        body = str(state.get("body") or "")
        assert "ChangeUsed" not in body
        assert "skillVERDICT" not in body and "skillRun" not in body
        live_lines = [ln for ln in live.splitlines() if ln.strip()]
        harvest_lines = [ln for ln in body.splitlines() if ln.strip()]
        assert len(harvest_lines) >= len(live_lines) - 3
        assert "$ULG_REPO: HOME=\"" in body
        cleaned = finalize_scrape_body(body)
        assert "Used toys integration" not in cleaned
        assert "3 minutes ago" not in cleaned
        assert "Claude responded:" not in cleaned
        assert "VERDICT: Change" in cleaned
        assert "$ULG_REPO" in cleaned
        assert "}" in cleaned
        assert "def f():" in cleaned
    finally:
        await browser.close()
        await pw.stop()
