"""Playwright DOM tests for anchored HARVEST_JS (offline)."""

from __future__ import annotations

import pytest

from claude_bundles.chat_reply_wait import HARVEST_JS
from claude_bundles.reply_anchor import USER_TURN_SELECTORS

pytest.importorskip("playwright")
from playwright.async_api import async_playwright  # noqa: E402

pytestmark = pytest.mark.offline

_MARKER = "our-sealed-prompt-marker-xyz"


def _anchor_args(**extra: object) -> dict:
    base = {
        "minMsgChars": 10,
        "anchorMarker": _MARKER,
        "priorMatches": 0,
        "userSelectors": list(USER_TURN_SELECTORS),
    }
    base.update(extra)
    return base


async def _harvest_html(html: str, *, args: dict | None = None) -> dict:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(html)
            return await page.evaluate(HARVEST_JS, args or {"minMsgChars": 10})
        finally:
            await browser.close()


async def _harvest_cowork_url(html: str, *, args: dict) -> dict:
    wrapped = f"<!doctype html><html><body>{html}</body></html>"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()

            async def _route(route):
                await route.fulfill(
                    status=200,
                    content_type="text/html",
                    body=wrapped,
                )

            await page.route("**/cowork/cse_fixture", _route)
            await page.goto("https://claude.ai/cowork/cse_fixture")
            return await page.evaluate(HARVEST_JS, args)
        finally:
            await browser.close()


_FIXTURE_A = f"""
<!doctype html><html><body>
<div data-testid="human-turn">Old prompt text from prior dispatch</div>
<div data-testid="assistant-message">Old assistant reply with enough characters.</div>
<div data-testid="human-turn"><h2>You said:</h2> {_MARKER}</div>
<div data-testid="assistant-message">Mid-loop prose before tools.\\nLoaded tools\\nLoaded tools</div>
<div data-testid="human-turn">Foreign operator steer without stamp</div>
<div data-testid="assistant-message">Steer answer prose here with Agent Bus label twice below.\\nAgent Bus\\nAgent Bus</div>
</body></html>
"""


@pytest.mark.asyncio
async def test_anchored_fixture_a_window_and_foreign_turns() -> None:
    state = await _harvest_html(_FIXTURE_A, args=_anchor_args())
    assert state.get("anchor_found") is True
    assert state.get("anchor_matches") == 1
    assert state.get("n") == 2
    assert "Steer answer prose" in state.get("body", "")
    assert state.get("foreign_user_turns") == 1


@pytest.mark.asyncio
async def test_anchored_prior_matches_blocks_until_new_turn() -> None:
    html = f"""
    <div data-testid="human-turn">{_MARKER}</div>
    <div data-testid="assistant-message">First reply with enough text here.</div>
    """
    blocked = await _harvest_html(html, args=_anchor_args(priorMatches=1))
    assert blocked.get("anchor_found") is False
    assert blocked.get("n") == 0
    html2 = html + f"""
    <div data-testid="human-turn">{_MARKER} second send</div>
    <div data-testid="assistant-message">Second reply with enough text here.</div>
    """
    found = await _harvest_html(html2, args=_anchor_args(priorMatches=1))
    assert found.get("anchor_found") is True
    assert "Second reply" in found.get("body", "")


@pytest.mark.asyncio
async def test_legacy_mode_unchanged_keys_without_anchor_marker() -> None:
    html = """
    <div data-testid="assistant-message">Legacy harvest body with sufficient length.</div>
    """
    state = await _harvest_html(html)
    assert "anchored" not in state
    assert state.get("n") == 1
    assert "Legacy harvest" in state.get("body", "")


@pytest.mark.asyncio
async def test_cowork_route_fixture_runs_extended_selectors() -> None:
    html = f"""
    <div data-testid="human-turn">You said: {_MARKER}</div>
    <div data-testid="assistant-turn">Cowork assistant window body long enough.</div>
    """
    state = await _harvest_cowork_url(html, args=_anchor_args())
    assert state.get("cowork_cse") is True
    assert state.get("anchor_found") is True


_FIXTURE_A_FINAL_REPLY = f"""
<!doctype html><html><body>
<div data-testid="human-turn">Old prompt text from prior dispatch</div>
<div data-testid="assistant-message">Old assistant reply with enough characters.</div>
<div data-testid="human-turn"><h2>You said:</h2> {_MARKER}</div>
<div data-testid="assistant-message">Mid-loop prose before tools.\\nLoaded tools\\nLoaded tools</div>
<div data-testid="human-turn">Foreign operator steer without stamp</div>
<div data-testid="assistant-message">Steer answer prose here with Agent Bus label twice below.\\nAgent Bus\\nAgent Bus</div>
<div data-testid="assistant-message">Final sealed answer prose after the steer turn.</div>
</body></html>
"""


@pytest.mark.asyncio
async def test_anchored_fixture_a_final_reply_is_body() -> None:
    state = await _harvest_html(_FIXTURE_A_FINAL_REPLY, args=_anchor_args())
    assert state.get("n") == 3
    assert "Final sealed answer" in state.get("body", "")


_NESTED_ASSISTANT_HTML = f"""
<!doctype html><html><body>
<div data-testid="human-turn"><h2>You said:</h2> {_MARKER}</div>
<div data-testid="assistant-message">
  <div data-testid="assistant-message">Inner assistant reply with enough characters here.</div>
</div>
<div class="font-claude-response">Later selector match with sufficient text length.</div>
</body></html>
"""


@pytest.mark.asyncio
async def test_anchored_nested_assistant_uses_page_order_last() -> None:
    state = await _harvest_html(_NESTED_ASSISTANT_HTML, args=_anchor_args())
    assert state.get("anchor_found") is True
    assert state.get("n") == 2
    assert "Later selector match" in state.get("body", "")


_STALE_CARD_HTML = f"""
<!doctype html><html><body>
<div data-testid="human-turn">{_MARKER}</div>
<div data-testid="assistant-message" data-cdp-artifact-card="0">
  Old reply with a stale artifact card stamp on the turn node itself.
</div>
<div data-testid="human-turn">{_MARKER} second</div>
<div data-testid="assistant-message">
  <button>Fresh bind document title here Document · MD</button>
  Current reply body long enough for harvest window selection.
</div>
</body></html>
"""


@pytest.mark.asyncio
async def test_anchored_clears_stale_card_stamp_on_old_turn() -> None:
    state = await _harvest_html(_STALE_CARD_HTML, args=_anchor_args(priorMatches=1))
    assert state.get("anchor_found") is True
    cards = state.get("artifact_cards") or []
    assert len(cards) == 1
    assert cards[0]["title"] == "Fresh bind document title here"


_INDUCTION_OUTSIDE_WINDOW = f"""
<!doctype html><html><body>
<div data-testid="assistant-message">Skill induction acknowledgement turn is here.</div>
<div data-testid="human-turn"><h2>You said:</h2> {_MARKER}</div>
<div data-testid="assistant-message">Answer to our sealed prompt with enough length.</div>
</body></html>
"""


@pytest.mark.asyncio
async def test_anchored_induction_turn_outside_window() -> None:
    state = await _harvest_html(_INDUCTION_OUTSIDE_WINDOW, args=_anchor_args())
    assert state.get("anchor_found") is True
    assert state.get("n") == 1
    assert "Answer to our sealed" in state.get("body", "")
    assert "induction acknowledgement" not in state.get("body", "")
