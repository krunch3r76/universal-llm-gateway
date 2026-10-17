from __future__ import annotations

import pytest

from research_dialogue.excerpts import FORBIDDEN_LINES, extract_lines_from_text

pytestmark = pytest.mark.offline


def test_forbidden_lines_never_selected() -> None:
    lines = [f"line-{i}" for i in range(1, 220)]
    out = extract_lines_from_text(lines)
    for n in FORBIDDEN_LINES:
        assert f"line-{n}" not in out.splitlines()


def test_selected_ranges_present() -> None:
    lines = [f"L{n}" for n in range(1, 220)]
    out = extract_lines_from_text(lines)
    assert "L3" in out
    assert "L11" in out
    assert "L98" in out
    assert "L203" in out
