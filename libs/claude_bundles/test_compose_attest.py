"""Offline tests for compose attestation + warm live submit discovery (25291)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("playwright")
from playwright.async_api import async_playwright  # noqa: E402

from claude_bundles.chat_cowork_mode import select_compose_mode
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


@pytest.mark.asyncio
async def test_select_compose_mode_polls_until_fingerprint_attests_cowork(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chip absent at t=0; fingerprint attests cowork during poll — not chip_missing."""
    fp_calls = {"n": 0}
    chat_fp = {
        "title": "New chat - Claude",
        "mode": "chat",
        "approval": None,
        "url": "https://claude.ai/new",
    }
    cowork_fp = {
        "title": "New task - Claude",
        "mode": "cowork",
        "approval": {"aria": "Automatically approve", "text": "Auto"},
        "url": "https://claude.ai/new",
    }

    async def mock_fingerprint(_page) -> dict:
        fp_calls["n"] += 1
        return chat_fp if fp_calls["n"] <= 2 else cowork_fp

    async def mock_try_click(_page, _label):
        return None, {"gate_rejects": [], "surface_radiogroup_count": 0, "radiogroup_names": []}

    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode.compose_mode_fingerprint",
        mock_fingerprint,
    )
    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode.try_click_compose_chip",
        mock_try_click,
    )
    page = AsyncMock()
    page.wait_for_timeout = AsyncMock()

    result = await select_compose_mode(page, "cowork")
    assert result["ok"] is True
    assert result["step"] == "already_cowork"
    assert result["after"]["mode"] == "cowork"


@pytest.mark.asyncio
async def test_select_compose_mode_polls_until_chip_click_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chip absent at t=0, present after poll — click then attest, not chip_missing."""
    click_calls = {"n": 0}
    chat_fp = {
        "title": "New chat - Claude",
        "mode": "chat",
        "approval": None,
        "url": "https://claude.ai/new",
    }
    cowork_fp = {
        "title": "New task - Claude",
        "mode": "cowork",
        "approval": {"aria": "Automatically approve", "text": "Auto"},
        "url": "https://claude.ai/new",
    }

    async def mock_fingerprint(_page) -> dict:
        return chat_fp

    async def mock_try_click(_page, _label):
        click_calls["n"] += 1
        probe = {
            "gate_rejects": [],
            "surface_radiogroup_count": 1,
            "radiogroup_names": ["Surface"],
        }
        if click_calls["n"] < 3:
            return None, probe
        return "playwright_surface", {**probe, "via": "playwright_surface"}

    async def mock_attest(_page, mode, *, timeout_s=8.0, poll_ms=400) -> dict:
        return {"ok": True, "step": f"attested_{mode}", "fingerprint": cowork_fp}

    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode.compose_mode_fingerprint",
        mock_fingerprint,
    )
    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode.try_click_compose_chip",
        mock_try_click,
    )
    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode.await_compose_attest",
        mock_attest,
    )
    page = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.evaluate = AsyncMock(return_value=[])

    result = await select_compose_mode(page, "cowork")
    assert result["ok"] is True
    assert result["step"] == "selected_cowork"
    assert result["step"] != "chip_missing"
