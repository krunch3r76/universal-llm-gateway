"""Executable HARVEST_JS stop falsifiers (24873 R-amendments 3–4)."""

from __future__ import annotations

import pytest

from claude_bundles.chat_reply_wait import HARVEST_JS, _in_flight

pytest.importorskip("playwright")
from playwright.async_api import async_playwright  # noqa: E402

pytestmark = pytest.mark.offline


_SIDEBAR_IDLE_HTML = """
<!doctype html><html><body>
<nav id="sidebar">
  <button role="button" aria-label="More options for Agent-bus 5237 Stop 8">⋯</button>
  <button role="button">Agent-bus 5237 Stop 8</button>
  <button role="button">Stop</button>
</nav>
<main>
  <div data-testid="assistant-message">Completed assistant reply with enough text for harvest.</div>
  <div data-testid="chat-input" contenteditable="true"></div>
</main>
</body></html>
"""

_GENERATION_ACTIVE_HTML = """
<!doctype html><html><body>
<nav id="sidebar">
  <button role="button" aria-label="More options for Agent-bus 5237 Stop 8">⋯</button>
  <button role="button">Stop</button>
</nav>
<main>
  <div data-testid="assistant-message" data-is-streaming="true">Partial streamed reply…</div>
  <button aria-label="Stop generating">Stop</button>
  <div data-testid="chat-input" contenteditable="true"></div>
</main>
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
async def test_harvest_stop_false_when_sidebar_only_pollution() -> None:
    """Neg falsifier: sidebar Stop threads must not set stop=true."""
    state = await _harvest(_SIDEBAR_IDLE_HTML)
    assert state["stop"] is False
    assert state["streaming"] is False


@pytest.mark.asyncio
async def test_harvest_stop_true_when_generation_control_in_main() -> None:
    """Pos falsifier: generation Stop in main must set stop=true."""
    state = await _harvest(_GENERATION_ACTIVE_HTML)
    assert state["stop"] is True
    assert state["streaming"] is True


def test_in_flight_true_when_streaming_even_if_stop_false() -> None:
    """streaming backstop keeps harvest in-flight when stop is momentarily false."""
    assert _in_flight({"streaming": True, "stop": False, "tool_pause": False})
