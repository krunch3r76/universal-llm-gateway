"""Pure-string tests for disposition lesson capture (journal-projection L0)."""

from __future__ import annotations

from claude_bundles.journal_lesson import (
    capture_lesson,
    journal_has_source_turn_id,
    should_append_lesson,
    writer_mode,
)


def _disposition_body(*extra: str) -> str:
    lines = ["TYPE: DISPOSITION", "status: complete", *extra]
    return "\n".join(lines) + "\n"


def test_lesson_line_captures_one() -> None:
    body = _disposition_body(
        "lesson: poll with poll_hint unchanged",
        "lesson: second line must not become a second capture",
    )
    capture = capture_lesson(body, turn_id="16671#3")
    assert capture is not None
    assert capture.turn_id == "16671#3"
    assert capture.lesson_line == "lesson: poll with poll_hint unchanged"


def test_disposition_without_lesson_captures_none() -> None:
    body = _disposition_body("next: dispatch the lane")
    assert capture_lesson(body, turn_id="16671#4") is None


def test_non_disposition_type_ignored() -> None:
    body = "\n".join(
        [
            "TYPE: DIRECTIVE",
            "lesson: must not capture when TYPE is not disposition",
        ]
    )
    assert capture_lesson(body, turn_id="16671#5") is None


def test_dedupe_on_source_turn_id() -> None:
    body = _disposition_body("lesson: one entry per turn")
    capture = capture_lesson(body, turn_id="16671#6")
    assert capture is not None
    journal = "## 2026-10-13 — prior\nsource_turn_id: 16671#6\n"
    assert journal_has_source_turn_id(journal, "16671#6")
    assert should_append_lesson(capture, journal_text=journal) is False
    assert should_append_lesson(capture, journal_text="") is True


def test_writer_mode_marker() -> None:
    assert writer_mode("") == "seat"
    assert writer_mode("## entry\nRULE: [route] durable\n") == "seat"
    assert writer_mode("Notes say projector_live: must be line-start\n") == "seat"
    assert writer_mode("- projector_live: abc-123\n") == "projector"
    assert writer_mode("**projector_live: abc-123\n") == "projector"
    assert writer_mode("- bullet **projector_live:** mid-bullet does not count\n") == "seat"


def test_midline_lesson_ignored() -> None:
    body = _disposition_body(
        "The seat wrote lesson: mid-sentence must not match",
        "- **A tool** that refuses **lesson:** inside a bullet must not match",
        "   - lesson: indented line-start keeps",
    )
    capture = capture_lesson(body, turn_id="16671#7")
    assert capture is not None
    assert capture.lesson_line == "   - lesson: indented line-start keeps"
