"""Pure-string tests for disposition lesson capture (journal-projection L0)."""

from __future__ import annotations

from claude_bundles.journal_lesson import (
    capture_lesson,
    check_induction,
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


def _maestro_journal_fixture() -> str:
    return (
        "Purpose preamble.\n"
        "## 2026-10-14 — entry 157\n"
        "RULE: [route] poll with poll_hint unchanged\n"
        "observed specimen token 16671#9 in this entry only\n"
        "## 2026-10-01 — entry 1\n"
        "RULE: [route] older rule\n"
        "misplaced specimen token 16671#other belongs under this heading\n"
    )


def _other_role_journal_fixture() -> str:
    return (
        "## 2026-10-14 — entry 157\n"
        "other-role-only specimen token 16671#other\n"
    )


def _registration_body(*extra: str) -> str:
    lines = ["TYPE: SEAT_REGISTRATION", *extra]
    return "\n".join(lines) + "\n"


def test_induction_ok() -> None:
    journal = _maestro_journal_fixture()
    body = _registration_body(
        "induction: RULE 2026-10-14 — entry 157 — I poll with poll_hint unchanged"
        " · specimen 16671#9",
    )
    assert check_induction(body, journal_text=journal) == "ok"


def test_induction_missing_entry() -> None:
    journal = _maestro_journal_fixture()
    body = _registration_body(
        "induction: RULE 2099-01-01 — missing entry — no such heading"
        " · specimen 16671#9",
    )
    assert check_induction(body, journal_text=journal) == "register_not_carried"


def test_induction_missing_specimen() -> None:
    journal = _maestro_journal_fixture()
    body = _registration_body(
        "induction: RULE 2026-10-14 — entry 157 — cites wrong entry body"
        " · specimen 16671#other",
    )
    assert check_induction(body, journal_text=journal) == "register_not_carried"


def test_induction_does_not_read_other_role_journal() -> None:
    maestro = _maestro_journal_fixture()
    other = _other_role_journal_fixture()
    body = _registration_body(
        "induction: RULE 2026-10-14 — entry 157 — specimen lives only elsewhere"
        " · specimen 16671#other",
    )
    assert check_induction(body, journal_text=maestro) == "register_not_carried"
    assert check_induction(body, journal_text=other) == "ok"


def test_midline_lesson_ignored() -> None:
    body = _disposition_body(
        "The seat wrote lesson: mid-sentence must not match",
        "- **A tool** that refuses **lesson:** inside a bullet must not match",
        "   - lesson: indented line-start keeps",
    )
    capture = capture_lesson(body, turn_id="16671#7")
    assert capture is not None
    assert capture.lesson_line == "   - lesson: indented line-start keeps"
