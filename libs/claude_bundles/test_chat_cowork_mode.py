"""Hermetic tests for Cowork/Auto mode helpers (no CDP)."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("playwright")
from playwright.async_api import async_playwright  # noqa: E402

from claude_bundles.chat_cowork_mode import (  # noqa: E402
    _APPROVAL_ARIA,
    _APPROVAL_MENU,
    _open_approval_menu,
    exclusive_radio_text_match,
    set_approval_mode,
)
from claude_bundles.compose_attest import (  # noqa: E402
    _compose_attested,
    cowork_auto_refuse_reason,
)

pytestmark = pytest.mark.offline

# a:31319 — approval chip with no aria-label at all, just a bare "Auto"
# button. No menu backs it (decoy shape) — clicking must not fabricate ok=True.
_ARIA_LESS_NO_MENU_HTML = """
<!doctype html><html><head><title>New task - Claude</title></head><body>
<button>Auto</button>
</body></html>
"""

# Same aria-less chip, but clicking it reveals a real menuitemradio group —
# the positive counterpart proving _open_approval_menu still works when a
# menu genuinely opens.
_ARIA_LESS_WITH_MENU_HTML = """
<!doctype html><html><head><title>New task - Claude</title></head><body>
<button id="chip">Auto</button>
<div id="menu" style="display:none">
  <div role="menuitemradio">Automatically approve</div>
  <div role="menuitemradio">Manually approve</div>
  <div role="menuitemradio">Skip all approvals</div>
</div>
<script>
document.getElementById('chip').addEventListener('click', () => {
  document.getElementById('menu').style.display = 'block';
});
</script>
</body></html>
"""


def test_approval_aria_patterns_match_live_labels() -> None:
    assert _APPROVAL_ARIA["auto"].search("Automatically approve")
    assert _APPROVAL_ARIA["manual"].search("Manually approve")
    assert not _APPROVAL_ARIA["auto"].search("Manually approve")


def test_approval_menu_patterns_match_radio_copy() -> None:
    assert _APPROVAL_MENU["auto"].search("Automatically approve")
    assert _APPROVAL_MENU["auto"].search("Auto")
    assert _APPROVAL_MENU["manual"].search("Manually approve")
    assert _APPROVAL_MENU["skip"].search("Skip all approvals")


def test_title_mode_heuristic() -> None:
    assert re.search(r"new task", "New task - Claude", re.I)
    assert re.search(r"new chat", "New chat - Claude", re.I)


def test_exclusive_radio_rejects_parent_group_concat() -> None:
    """Friction 24610 — Manual+Auto parent group must not match auto token."""
    group = "Manually approve Automatically approve Skip all approvals"
    assert not exclusive_radio_text_match(group, "Automatically approve")
    assert exclusive_radio_text_match(
        "⚡ Automatically approve", "Automatically approve"
    )
    assert exclusive_radio_text_match("Manually approve", "Manually approve")
    assert not exclusive_radio_text_match("Manually approve", "Automatically approve")


def test_compose_attested_cowork_manual_must_fail() -> None:
    """Ship gate: Cowork+Manual is not attested success (OR hole closed)."""
    fp = {
        "title": "New task - Claude",
        "mode": "cowork",
        "approval": {"aria": "Manually approve", "text": "Manual"},
        "url": "https://claude.ai/new",
    }
    assert _compose_attested(fp, "cowork") is False
    assert _compose_attested(fp, "cowork", require_auto=False) is True
    reason = cowork_auto_refuse_reason(fp)
    assert reason is not None
    assert "Manually approve" in reason
    assert "Automatically approve" in reason


def test_compose_attested_cowork_skip_must_fail() -> None:
    fp = {
        "mode": "cowork",
        "approval": {"aria": "Skip all approvals", "text": "Skip"},
    }
    assert _compose_attested(fp, "cowork") is False
    reason = cowork_auto_refuse_reason(fp)
    assert reason is not None
    assert "Skip all approvals" in reason


def test_compose_attested_cowork_auto_passes() -> None:
    fp = {
        "title": "New task - Claude",
        "mode": "cowork",
        "approval": {"aria": "Automatically approve", "text": "Auto"},
        "url": "https://claude.ai/new",
    }
    assert _compose_attested(fp, "cowork") is True
    assert cowork_auto_refuse_reason(fp) is None


def test_compose_attested_project_shell_without_chips_skips() -> None:
    """Named exception: Project shell has no Chat/Cowork/Auto chips."""
    fp = {
        "title": "Project - Claude",
        "mode": None,
        "approval": None,
        "url": "https://claude.ai/project/abc",
    }
    assert cowork_auto_refuse_reason(fp) is None


def test_compose_attested_chat_without_approval_skips() -> None:
    fp = {
        "title": "New chat - Claude",
        "mode": "chat",
        "approval": None,
        "url": "https://claude.ai/new",
    }
    assert _compose_attested(fp, "chat") is True
    assert cowork_auto_refuse_reason(fp) is None


def test_approval_aria_patterns_match_short_text_labels() -> None:
    """a:31319 — chip can render aria-less with just the bare state word."""
    assert _APPROVAL_ARIA["auto"].search("Auto")
    assert _APPROVAL_ARIA["manual"].search("Manual")
    assert _APPROVAL_ARIA["skip"].search("Skip")
    assert not _APPROVAL_ARIA["auto"].search("Manual")
    assert not _APPROVAL_ARIA["auto"].search("Automate")  # exact-word, not prefix


@pytest.mark.asyncio
async def test_set_approval_mode_already_auto_via_text_label_skips_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct regression for a:31319 — the dominant real-world path never
    reaches _open_approval_menu once the fingerprint sees the text-only
    "Auto" label as already attested."""
    text_only_auto_fp = {
        "title": "New task - Claude",
        "mode": "cowork",
        "approval": {"aria": "", "text": "Auto", "via": "text"},
        "url": "https://claude.ai/new",
    }

    async def mock_fingerprint(_page) -> dict:
        return text_only_auto_fp

    opened_menu = AsyncMock()
    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode.compose_mode_fingerprint",
        mock_fingerprint,
    )
    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode._open_approval_menu",
        opened_menu,
    )
    page = AsyncMock()

    result = await set_approval_mode(page, "auto")
    assert result["ok"] is True
    assert result["step"] == "already_auto"
    opened_menu.assert_not_called()


@pytest.mark.asyncio
async def test_open_approval_menu_rejects_click_that_does_not_open_menu() -> None:
    """False-positive-click guard — a click landing on a decoy "Auto" button
    with no backing menu must not report ok=True (pre-fix behavior did)."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_ARIA_LESS_NO_MENU_HTML)
            result = await _open_approval_menu(page)
            assert result["ok"] is False
            assert result["step"] == "approval_control_missing"
            assert any(c.get("text") == "Auto" for c in result["candidates"])
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_open_approval_menu_accepts_click_that_opens_menu() -> None:
    """Positive counterpart — a click that genuinely opens the menu still
    reports ok=True via the text fallback (aria-less chip)."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_ARIA_LESS_WITH_MENU_HTML)
            result = await _open_approval_menu(page)
            assert result["ok"] is True
            assert result["opened_via"] == "text"
        finally:
            await browser.close()
