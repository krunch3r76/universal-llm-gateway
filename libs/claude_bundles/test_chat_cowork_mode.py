"""Hermetic tests for Cowork/Auto mode helpers (no CDP)."""

from __future__ import annotations

import re

from claude_bundles.chat_cowork_mode import (
    _APPROVAL_ARIA,
    _APPROVAL_MENU,
    exclusive_radio_text_match,
)


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
    assert exclusive_radio_text_match("⚡ Automatically approve", "Automatically approve")
    assert exclusive_radio_text_match("Manually approve", "Manually approve")
    assert not exclusive_radio_text_match("Manually approve", "Automatically approve")
