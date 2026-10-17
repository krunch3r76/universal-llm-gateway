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
    assert "L166" not in out.splitlines()
    assert "L197" not in out.splitlines()


def test_read_excerpts_sha_mismatch(tmp_path) -> None:
    from research_dialogue.excerpts import read_excerpts

    bad = tmp_path / "bad.md"
    bad.write_text("# not the deliverable\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sha256 mismatch"):
        read_excerpts(bad)
