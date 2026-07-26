"""Unit tests for distill_index_line."""

from session_store.distill import distill_index_line


def test_distill_collapses_newlines() -> None:
    line = distill_index_line(3, "user", "line one\nline two\nline three")
    assert "\n" not in line
    assert line.startswith("0003 user:")


def test_distill_max_chars() -> None:
    body = "x" * 200
    line = distill_index_line(1, "assistant", body, max_chars=120)
    assert len(line) <= 120


def test_distill_hostile_input_single_line_bounded() -> None:
    long_first = "A" * 180
    body = f"{long_first}\nsecond line\rthird with lone CR\rfourth"
    line = distill_index_line(7, "assistant", body, max_chars=120)
    assert "\n" not in line
    assert "\r" not in line
    assert len(line) <= 120
    assert line.startswith("0007 assistant:")
    assert line.endswith("…")
