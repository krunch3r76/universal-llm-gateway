"""Web-seat session-close orientation block on boot briefing card."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools._boot_helpers._briefing_card import render_briefing_card


def test_claude_web_boot_card_inlines_session_close_protocol() -> None:
    card, _manifest = render_briefing_card(
        family="claude",
        agent="claude-web",
    )
    assert "Session Close — MANDATORY" in card
    assert "session-close-kernel" in card
    assert "close(op=stage|draft|check|commit)" in card
    assert "Life/web primary" in card
    assert "Cursor exception" in card
    assert "web-transcript-preprocessing" in card
    assert "agent-skills/" not in card
    assert "Use the `" in card
    assert " skill" in card


def test_claude_cursor_boot_card_omits_web_session_close_block() -> None:
    card, _manifest = render_briefing_card(
        family="claude",
        agent="claude-cursor",
    )
    assert "Session Close — MANDATORY" not in card
