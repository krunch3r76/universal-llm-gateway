"""Deterministic lesson capture from maestro DISPOSITION turns (L0)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from agent_bus_store.disposition import body_has_disposition_type
from maestro_induct.parse import parse_journal_entries

# Same optional bullet / bold prefix rule as ``RULE:`` in ``journal_hop_projection``.
_LESSON_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:\*\*)?lesson:",
    re.IGNORECASE,
)
_PROJECTOR_LIVE_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:\*\*)?projector_live:",
    re.IGNORECASE,
)
_SOURCE_TURN_ID_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:\*\*)?source_turn_id:\s*(\S+)",
    re.IGNORECASE,
)
_INDUCTION_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:\*\*)?induction:",
    re.IGNORECASE,
)
_INDUCTION_RULE_PREFIX_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:\*\*)?induction:\s*RULE\s+(.*)$",
    re.IGNORECASE | re.DOTALL,
)
_SPECIMEN_SUFFIX = " · specimen "

WriterMode = Literal["seat", "projector"]
InductionResult = Literal["ok", "register_not_carried"]


@dataclass(frozen=True)
class LessonCapture:
    """One journal entry candidate from a single disposition turn."""

    turn_id: str
    lesson_line: str


def _line_has_lesson_prefix(line: str) -> bool:
    return _LESSON_LINE_RE.match(line or "") is not None


def _lesson_lines(body: str) -> list[str]:
    kept: list[str] = []
    for line in (body or "").splitlines():
        if _line_has_lesson_prefix(line):
            kept.append(line.rstrip())
    return kept


def parse_source_turn_id(body: str) -> str | None:
    """Line-start ``source_turn_id:`` token from a disposition or closeout body."""
    for line in (body or "").splitlines():
        match = _SOURCE_TURN_ID_RE.match(line)
        if match:
            return match.group(1)
    return None


def capture_lesson(body: str, *, turn_id: str) -> LessonCapture | None:
    """Return one capture when *body* is a disposition with line-start ``lesson:``."""
    if not body_has_disposition_type(body):
        return None
    lines = _lesson_lines(body)
    if not lines:
        return None
    return LessonCapture(turn_id=turn_id, lesson_line=lines[0])


def writer_mode(journal_text: str) -> WriterMode:
    """``seat`` until a line-start ``projector_live:`` marker exists in the journal."""
    text = journal_text or ""
    for line in text.splitlines():
        if _PROJECTOR_LIVE_LINE_RE.match(line):
            return "projector"
    return "seat"


def journal_has_source_turn_id(journal_text: str, turn_id: str) -> bool:
    """True when the journal already records this bus turn as appended."""
    needle = (turn_id or "").strip()
    if not needle:
        return False
    for line in (journal_text or "").splitlines():
        match = _SOURCE_TURN_ID_RE.match(line)
        if match and match.group(1) == needle:
            return True
    return False


def should_append_lesson(capture: LessonCapture, *, journal_text: str) -> bool:
    """False when the dedupe key (source bus turn id) is already in the journal."""
    return not journal_has_source_turn_id(journal_text, capture.turn_id)


def _line_has_induction_prefix(line: str) -> bool:
    return _INDUCTION_LINE_RE.match(line or "") is not None


def _first_induction_line(body: str) -> str | None:
    for line in (body or "").splitlines():
        if _line_has_induction_prefix(line):
            return line.rstrip()
    return None


def _journal_entries_by_title(journal_text: str) -> dict[str, str]:
    entries = parse_journal_entries(journal_text or "", limit=10_000)
    return {item["title"]: item["body"] for item in entries}


def check_induction(text: str, *, journal_text: str) -> InductionResult:
    """Validate seat-authored ``induction:`` against one role journal (L3, test-only)."""
    line = _first_induction_line(text)
    if line is None:
        return "register_not_carried"
    match = _INDUCTION_RULE_PREFIX_RE.match(line)
    if not match:
        return "register_not_carried"
    rest = match.group(1).strip()
    if _SPECIMEN_SUFFIX not in rest:
        return "register_not_carried"
    before_specimen, specimen = rest.rsplit(_SPECIMEN_SUFFIX, 1)
    specimen = specimen.strip()
    if not specimen:
        return "register_not_carried"

    by_title = _journal_entries_by_title(journal_text)
    if not by_title:
        return "register_not_carried"

    entry_id: str | None = None
    for title in sorted(by_title, key=len, reverse=True):
        if before_specimen == title:
            entry_id = title
            break
        prefix = f"{title} — "
        if before_specimen.startswith(prefix):
            entry_id = title
            break
    if entry_id is None:
        return "register_not_carried"

    entry_body = by_title[entry_id]
    if specimen not in entry_body:
        return "register_not_carried"
    return "ok"
