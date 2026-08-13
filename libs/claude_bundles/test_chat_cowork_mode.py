"""Hermetic tests for Cowork/Auto mode helpers (no CDP)."""

from __future__ import annotations

import re

import pytest

from claude_bundles.chat_cowork_mode import (
    _APPROVAL_ARIA,
    _APPROVAL_MENU,
    exclusive_radio_text_match,
)
from claude_bundles.compose_attest import (
    _compose_attested,
    cowork_auto_refuse_reason,
)

pytestmark = pytest.mark.offline


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
