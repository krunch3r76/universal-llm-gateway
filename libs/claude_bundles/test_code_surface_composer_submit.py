"""Playwright tests for /code visible ProseMirror composer vs hidden chat-input (41186)."""

from __future__ import annotations

import pytest

pytest.importorskip("playwright")
from playwright.async_api import async_playwright  # noqa: E402

from claude_bundles.composer_submit import (
    assert_committed_user_turn,
    prove_composer_submitted,
    read_composer_draft,
    submit_composer_content,
    verification_marker,
)

pytestmark = pytest.mark.offline

_CODE_COMPOSER_HTML = """
<!doctype html><html><head><title>Code - Claude</title></head><body>
<div data-testid="chat-input" contenteditable="true" style="display:none"></div>
<div style="padding:40px">
  <div contenteditable="true" role="textbox" aria-label="Prompt"
       id="live-composer" style="min-height:80px;width:420px;border:1px solid #ccc">
  </div>
  <button type="button" aria-label="Send" id="send-btn">Send</button>
</div>
<script>
  const composer = document.getElementById('live-composer');
  const send = document.getElementById('send-btn');
  send.addEventListener('click', () => {
    const t = (composer.innerText || '').trim();
    if (!t) return;
    const turn = document.createElement('div');
    turn.setAttribute('data-testid', 'human-turn');
    turn.innerText = t;
    document.body.appendChild(turn);
    composer.innerText = '';
  });
  composer.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send.click();
    }
  });
</script>
</body></html>
"""


@pytest.mark.asyncio
async def test_read_draft_uses_visible_prosemirror_not_hidden_chat_input() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_CODE_COMPOSER_HTML)
        composer = page.locator("#live-composer")
        await composer.click()
        await page.keyboard.insert_text("draft in live composer")
        page_draft = await read_composer_draft(page)
        el_draft = await read_composer_draft(page, composer=composer)
        assert page_draft["len"] > 0
        assert "live composer" in page_draft["text"]
        assert el_draft["text"] == page_draft["text"]
        await browser.close()


@pytest.mark.asyncio
async def test_code_surface_submit_clears_live_composer_and_commits_turn() -> None:
    prompt = "#1-unique: code-submit-canary\nReply OK"
    needle = verification_marker(prompt)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_CODE_COMPOSER_HTML)
        composer = page.locator("#live-composer")
        await composer.click()
        await page.keyboard.insert_text(prompt)
        await submit_composer_content(page, prompt, composer=composer)
        draft = await read_composer_draft(page, composer=composer)
        assert draft["len"] == 0
        committed = await page.locator('[data-testid="human-turn"]').inner_text()
        assert needle in committed
        await browser.close()


_MULTILINE_NO_UNIQUE_PROMPT = (
    "Use the x skill\n\n\n\nledger_row: abc\n\npurpose: (unstated)\n\n"
    "<role>You are the S2 consultant with enough body to slice</role>"
)


@pytest.mark.asyncio
async def test_code_surface_multiline_submit_enters_and_commits_without_unique_marker() -> (
    None
):
    """Regression 41301: false release skips Enter; assert_committed_user_turn fails."""
    prompt = _MULTILINE_NO_UNIQUE_PROMPT
    needle = verification_marker(prompt)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_CODE_COMPOSER_HTML)
        composer = page.locator("#live-composer")
        await composer.click()
        await page.keyboard.insert_text(prompt)
        await submit_composer_content(page, prompt, composer=composer)
        draft = await read_composer_draft(page, composer=composer)
        assert draft["len"] == 0
        await assert_committed_user_turn(page, prompt, composer=composer)
        committed = " ".join(
            (await page.locator('[data-testid="human-turn"]').inner_text()).split()
        )
        assert needle in committed
        await browser.close()


_NO_SEND_ENTER_HTML = """
<!doctype html><html><head><title>Code - Claude</title></head><body>
<div contenteditable="true" role="textbox" id="live-composer"
     style="min-height:80px;width:420px;border:1px solid #ccc"></div>
<script>
  const composer = document.getElementById('live-composer');
  composer.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      const t = (composer.innerText || '').trim();
      if (!t) return;
      const turn = document.createElement('div');
      turn.setAttribute('data-testid', 'human-turn');
      turn.innerText = t;
      document.body.appendChild(turn);
      composer.innerText = '';
    }
  });
</script>
</body></html>
"""


@pytest.mark.asyncio
async def test_long_prompt_tail_needle_submits_via_enter_without_send_button() -> None:
    """Regression 41500: 800-char draft slice false-cleared; Enter must commit tail needle."""
    needle = "#1-unique: tail"
    prompt = ("a" * 2800) + needle + ("b" * (3000 - 2800 - len(needle)))
    assert len(prompt) == 3000
    assert prompt.index(needle) == 2800
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_NO_SEND_ENTER_HTML)
        composer = page.locator("#live-composer")
        await composer.click()
        await page.keyboard.insert_text(prompt)
        await submit_composer_content(page, prompt, composer=composer)
        draft = await read_composer_draft(page, composer=composer)
        assert draft["len"] == 0
        committed = await page.locator('[data-testid="human-turn"]').inner_text()
        assert needle in committed
        await browser.close()


@pytest.mark.asyncio
async def test_prove_does_not_skip_send_when_hidden_chat_input_empty() -> None:
    """Regression: page-level draft read must not false-clear on hidden stub."""
    prompt = "#2-unique: must-not-skip\nbody"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_CODE_COMPOSER_HTML)
        composer = page.locator("#live-composer")
        await composer.click()
        await page.keyboard.insert_text(prompt)
        await prove_composer_submitted(page, prompt, composer=composer)
        assert await page.locator('[data-testid="human-turn"]').count() == 1
        await browser.close()
