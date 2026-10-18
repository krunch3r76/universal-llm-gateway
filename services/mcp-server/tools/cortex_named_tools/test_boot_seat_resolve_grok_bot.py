"""Tests for `resolve_boot_family_platform(seat='grok-bot')` (a:39611)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.cortex_named_tools._boot_seat_resolve import resolve_boot_family_platform


def test_resolve_boot_family_platform_grok_bot() -> None:
    assert resolve_boot_family_platform(seat="grok-bot") == ("grok", "bot")
    assert resolve_boot_family_platform(seat="grok-bot", mount_surface="code") == (
        "grok",
        "bot",
    )
