"""cortex_boot agent= seat-slug resolution."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.cortex_named_tools._orchestration_tools import _resolve_boot_family_platform


def test_agent_cursor_alias() -> None:
    assert _resolve_boot_family_platform(agent="cursor") == ("claude", "cursor")


def test_agent_claude_web_slug() -> None:
    assert _resolve_boot_family_platform(agent="claude-web") == ("claude", "web")


def test_agent_grok_direct() -> None:
    assert _resolve_boot_family_platform(agent="grok-direct") == ("grok", "direct")


def test_family_platform_when_agent_absent() -> None:
    assert _resolve_boot_family_platform(family="grok", platform="api-multi") == (
        "grok",
        "api-multi",
    )


def test_agent_overrides_family_platform() -> None:
    assert _resolve_boot_family_platform(
        agent="claude-web", family="grok", platform="cursor"
    ) == ("claude", "web")
