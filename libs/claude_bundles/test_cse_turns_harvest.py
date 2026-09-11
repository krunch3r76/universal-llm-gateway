"""Offline tests for ordered CSE turn DOM extraction."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from claude_bundles.cse_turns_harvest import CSE_TURNS_JS, harvest_turns


@pytest.mark.offline
def test_cse_turns_js_source_present() -> None:
    assert "afterTurn" in CSE_TURNS_JS
    assert "limit" in CSE_TURNS_JS
    assert 'data-testid="transcript-row"' in CSE_TURNS_JS
    assert 'data-testid="user-message"' in CSE_TURNS_JS
    assert "slice(-" in CSE_TURNS_JS
    assert "scrollTop" in CSE_TURNS_JS
    assert 'data-testid*="user"' not in CSE_TURNS_JS
    assert "slice(0," not in CSE_TURNS_JS


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
            "truncated": len(selected)
            < len([o for o in ordinals if after_turn is None or o > int(after_turn)]),
        }

    page = AsyncMock()
    page.evaluate = fake_evaluate

    result = await harvest_turns(page, limit=2, after_turn=2)

    assert captured == {"limit": 2, "afterTurn": 2}
    assert [row["ordinal"] for row in result["turns"]] == [3, 4]
    assert result["turns"][0]["text"].startswith("Turn 3")
    assert result["turns"][1]["text"].startswith("Turn 4")
    assert result["truncated"] is True


@pytest.mark.offline
@pytest.mark.asyncio
async def test_harvest_turns_transcript_row_full_coverage() -> None:
    async def fake_evaluate(_js: str, args: dict[str, object]) -> dict[str, object]:
        return {
            "turns": [
                {
                    "author": "assistant",
                    "timestamp": "2026-09-10T12:00:00Z",
                    "text": "First assistant reply with enough text here.",
                    "ordinal": 1,
                },
                {
                    "author": "user",
                    "timestamp": "2026-09-10T12:01:00Z",
                    "text": "User question with enough text here.",
                    "ordinal": 2,
                },
                {
                    "author": "assistant",
                    "timestamp": "2026-09-10T12:02:00Z",
                    "text": "Second assistant reply with enough text here.",
                    "ordinal": 3,
                },
                {
                    "author": "user",
                    "timestamp": "2026-09-10T12:03:00Z",
                    "text": "Follow-up user turn with enough text.",
                    "ordinal": 4,
                },
            ],
            "coverage": "full",
            "scroll_iterations": 3,
            "first_row_author": "assistant",
            "streaming": False,
            "stop": False,
            "tool_pause": False,
            "title": "Session - Claude",
            "spinner": False,
            "aria_busy": False,
            "truncated": False,
        }

    page = AsyncMock()
    page.evaluate = fake_evaluate

    result = await harvest_turns(page, limit=10)

    assert [row["author"] for row in result["turns"]] == [
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert [row["ordinal"] for row in result["turns"]] == [1, 2, 3, 4]
    assert result["coverage"] == "full"
    assert result["first_row_author"] == "assistant"
    assert result["scroll_iterations"] == 3


@pytest.mark.offline
@pytest.mark.asyncio
async def test_harvest_turns_zero_rows_fallback_tail() -> None:
    async def fake_evaluate(_js: str, _args: dict[str, object]) -> dict[str, object]:
        return {
            "turns": [
                {
                    "author": "assistant",
                    "timestamp": None,
                    "text": "Fallback assistant reply with enough text.",
                    "ordinal": 1,
                }
            ],
            "coverage": "tail",
            "scroll_iterations": 0,
            "first_row_author": "assistant",
            "streaming": False,
            "stop": False,
            "tool_pause": False,
            "title": "Session - Claude",
            "spinner": False,
            "aria_busy": False,
            "truncated": False,
        }

    page = AsyncMock()
    page.evaluate = fake_evaluate

    result = await harvest_turns(page, limit=10)

    assert result["coverage"] == "tail"
    assert result["scroll_iterations"] == 0


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
