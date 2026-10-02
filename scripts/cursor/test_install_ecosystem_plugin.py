"""Offline checks for install-ecosystem-plugin skill directory staging."""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_INSTALL = _REPO / "scripts" / "cursor" / "install-ecosystem-plugin.sh"
_REASONING = _REPO / "cursor-plugins" / "ulg-ecosystem" / "skills" / "reasoning-posture"
_CONDUCTOR = _REPO / "cursor-plugins" / "ulg-ecosystem" / "skills" / "conductor"


@pytest.mark.offline
def test_reasoning_posture_reference_companion_present() -> None:
    skill = _REASONING / "SKILL.md"
    ref = _REASONING / "reference.md"
    assert skill.is_file()
    assert ref.is_file()
    body = skill.read_text(encoding="utf-8")
    assert "## Six rules" not in body
    assert "reference.md beside this file" in body


@pytest.mark.offline
def test_conductor_reference_siblings_present_in_sot() -> None:
    refs = list(_CONDUCTOR.glob("reference-*.md"))
    assert len(refs) >= 5
