"""Offline tests for compose attestation + warm live submit discovery (25291)."""

from __future__ import annotations

import pytest

pytest.importorskip("playwright")
from playwright.async_api import async_playwright  # noqa: E402

from claude_bundles.compose_attest import (
    discover_live_submit,
    is_excluded_submit_control,
    is_positive_submit_match,
    pick_submit_candidate,
    resolve_submit_strategy,
    warm_submit_settle_ms,
)

pytestmark = pytest.mark.offline


_WARM_CSE_HTML = """
<!doctype html><html><head><title>Mid-session task - Claude</title></head><body>
<main>
  <div class="compose-shell">
    <button aria-label="Model: Sonnet 5 High">Sonnet 5</button>
    <button aria-label="Automatically approve">Auto</button>
    <button aria-label="Attach file">Attach</button>
    <div data-testid="chat-input" contenteditable="true" style="min-height:80px;width:400px"></div>
    <button aria-label="Send"></button>
  </div>
</main>
</body></html>
"""

_BARE_NEW_COWORK_HTML = """
<!doctype html><html><head><title>New task - Claude</title></head><body>
<main>
  <button aria-label="Model: Opus 4.8 High">Opus</button>
  <button aria-label="Automatically approve">Auto</button>
  <div data-testid="chat-input" contenteditable="true" style="min-height:80px;width:400px"></div>
  <button aria-label="Start task">Start task</button>
</main>
</body></html>
"""


def test_resolve_submit_strategy_warm_cse() -> None:
    url = "https://claude.ai/cowork/cse_01MB8QV81GvCcRLZkX1F5MJY"
    assert resolve_submit_strategy(url, {"mode": None}) == "live_discover"


def test_resolve_submit_strategy_warm_chat() -> None:
    assert resolve_submit_strategy("https://claude.ai/chat/abc", {}) == "live_discover"


def test_resolve_submit_strategy_bare_new() -> None:
    assert resolve_submit_strategy("https://claude.ai/new", {"mode": "cowork"}) == "mode_locked"


def test_is_excluded_submit_control_model_and_approval() -> None:
    assert is_excluded_submit_control(aria="Model: Sonnet 5 High")
    assert is_excluded_submit_control(aria="Automatically approve")
    assert is_excluded_submit_control(aria="Attach file")
    assert is_excluded_submit_control(aria="Stop generating")


def test_is_positive_submit_match_warm_send() -> None:
    assert is_positive_submit_match(aria="Send")
    assert is_positive_submit_match(aria="Send message")
    assert is_positive_submit_match(aria="Start task")
    assert not is_positive_submit_match(aria="Attach file")


def test_pick_submit_candidate_prefers_send_over_attach() -> None:
    candidates = [
        {"aria": "Attach file", "text": "", "name": "Attach file"},
        {"aria": "Send", "text": "", "name": "Send"},
    ]
    hit = pick_submit_candidate(candidates)
    assert hit is not None
    assert hit["name"] == "Send"
    assert hit["pick"] == "positive_match"


def test_pick_submit_candidate_unique_non_excluded() -> None:
    candidates = [
        {"aria": "Attach file", "text": "", "name": "Attach file"},
        {"aria": "", "text": "Go", "name": "Go"},
    ]
    hit = pick_submit_candidate(candidates)
    assert hit is not None
    assert hit["name"] == "Go"
    assert hit["pick"] == "unique_candidate"


def test_pick_submit_candidate_ambiguous_returns_none() -> None:
    candidates = [
        {"aria": "", "text": "Go", "name": "Go"},
        {"aria": "", "text": "Next", "name": "Next"},
    ]
    assert pick_submit_candidate(candidates) is None


def test_warm_submit_settle_ms_documented() -> None:
    assert warm_submit_settle_ms() == 300


@pytest.mark.asyncio
async def test_discover_live_submit_warm_cse_finds_send() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_WARM_CSE_HTML)
            hit = await discover_live_submit(page)
            assert hit["ok"] is True
            assert hit["via"] == "composer_local"
            assert hit["name"] == "Send"
            assert hit["pick"] == "positive_match"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_discover_live_submit_bare_new_finds_start_task() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_BARE_NEW_COWORK_HTML)
            hit = await discover_live_submit(page)
            assert hit["ok"] is True
            assert hit["name"] == "Start task"
        finally:
            await browser.close()
