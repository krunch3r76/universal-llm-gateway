"""Work UI commit verify against on-disk chatgpt_composer fixture."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from web_tab.provider_selectors import verify_committed_js

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "chatgpt_composer.html"
_PROMPT = "web_tab smoke ping: reply with the single word PONG"


@pytest.mark.asyncio
async def test_work_fixture_yields_dom_committed_for_existing_user_turn() -> None:
    html = _FIXTURE.read_text(encoding="utf-8")
    js = verify_committed_js(
        site="chatgpt", prompt_text=_PROMPT, baseline_user_count=0
    )
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        await page.evaluate(
            "() => { const c = document.querySelector('.ProseMirror'); if (c) c.textContent = ''; }"
        )
        result = await page.evaluate(js)
        await browser.close()
    assert isinstance(result, dict)
    assert result.get("receipt") == "dom_committed"
    assert result.get("has_user_turn") is True
