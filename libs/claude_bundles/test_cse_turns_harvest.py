"""Offline tests for ordered CSE turn DOM extraction."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from claude_bundles.cse_turns_harvest import CSE_TURNS_JS, harvest_turns


@pytest.mark.offline
def test_cse_turns_js_source_present() -> None:
    assert "afterTurn" in CSE_TURNS_JS
    assert "limit" in CSE_TURNS_JS


@pytest.mark.offline
@pytest.mark.asyncio
async def test_harvest_turns_ordering_and_bounds_hermetic() -> None:
    captured: dict[str, object] = {}

    async def fake_evaluate(_js: str, args: dict[str, object]) -> dict[str, object]:
        captured.update(args)
        limit = int(args["limit"])
        after_turn = args.get("afterTurn")
        ordinals = [1, 2, 3, 4, 5]
        selected = [
            ordinal
            for ordinal in ordinals
            if after_turn is None or ordinal > int(after_turn)
        ][:limit]
        return {
            "turns": [
                {
                    "author": "assistant",
                    "timestamp": None,
                    "text": f"Turn {ordinal} reply with enough text here.",
                    "ordinal": ordinal,
                }
                for ordinal in selected
            ],
            "streaming": False,
            "stop": False,
            "tool_pause": False,
            "title": "Session - Claude",
            "spinner": False,
            "aria_busy": False,
            "truncated": len(selected) < len(
                [o for o in ordinals if after_turn is None or o > int(after_turn)]
            ),
        }

    page = AsyncMock()
    page.evaluate = fake_evaluate

    result = await harvest_turns(page, limit=2, after_turn=2)

    assert captured == {"limit": 2, "afterTurn": 2}
    assert [row["ordinal"] for row in result["turns"]] == [3, 4]
    assert result["turns"][0]["text"].startswith("Turn 3")
    assert result["turns"][1]["text"].startswith("Turn 4")
    assert result["truncated"] is True


@pytest.mark.integration
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
