"""Regression: Agent Rules manifest block removed from boot briefing cards."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools._boot_helpers._briefing_card import render_briefing_card
from tools._boot_helpers._orientation_blocks import render_orientation_blocks
from tools.cortex_named_tools._boot_data_fetch import build_futures_spec


class _StubRecorder:
    def wrap(self, _name: object, fn: object) -> object:
        return fn


def test_build_futures_spec_omits_rules_layer_fetch() -> None:
    spec = build_futures_spec("claude-cursor", {}, _StubRecorder())
    assert "rules" not in spec
    assert "layer=rules" not in str(spec)


def test_render_briefing_card_omits_agent_rules_section() -> None:
    card, _manifest = render_briefing_card(
        skills_card_markdown="## Agent Skills\n> Load on demand",
    )
    assert "## Agent Rules" not in card
    assert "### Relevant now" not in card


def test_session_close_block_notes_platform_delivery_on_web() -> None:
    blocks = render_orientation_blocks(family="claude", agent="claude-web")
    session_close = next(b for b in blocks if "Session Close" in b)
    assert "Use the" in session_close and "skill" in session_close
    assert "session-close-kernel" in session_close
    assert "close(op=stage|draft|check|commit)" in session_close
    assert "Life/web primary" in session_close
    assert "Cursor exception" in session_close
    assert "session-close-audit" in session_close
    assert "web-transcript-preprocessing" in session_close
    assert "agent-skills/" not in session_close
    assert 'fs(sandbox="workspaces"' not in session_close
    assert 'fs(sandbox="cortex"' not in session_close
    assert "Use the `" in session_close
    assert " skill" in session_close
    # ¬ exclusive session_close steer without Cursor exception
    assert "Before `cortex(tool=\"session_close\"" not in session_close
