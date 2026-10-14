"""Deterministic maestro journal projection for the hop-successor body."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final

from implement_admission.closeout_helpers import cortex_files_root

MAESTRO_JOURNAL_URI: Final = "cortex://notes/system/maestro/journal.md"
MAESTRO_REGISTER_URI: Final = "cortex://notes/system/maestro/register.md"
LESSONS_K: Final = 5

MAESTRO_ROLE_FACETS: frozenset[str] = frozenset(
    {"route", "restart", "review", "brief", "bus", "land", "register"}
)

_RULE_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:\*\*)?RULE:",
    re.MULTILINE,
)
_FACET_TAG_RE = re.compile(r"\[([a-z]+)\]")
_ENTRY_HEADING_RE = re.compile(r"(?m)^## (.+?)\s*\n")
_STANDING_RULE_HEADING = "STANDING RULE"


def role_facet_set(role: str) -> frozenset[str]:
    """Facet tags kept for *role*; only maestro has a seed set (Q2)."""
    if (role or "").strip().lower() == "maestro":
        return MAESTRO_ROLE_FACETS
    return frozenset()


def _journal_entries(text: str) -> list[tuple[str, str, int]]:
    """``(heading, body, start_offset)`` in file order (newest first at head)."""
    body = text or ""
    matches = list(_ENTRY_HEADING_RE.finditer(body))
    if not matches:
        return []
    entries: list[tuple[str, str, int]] = []
    for index, match in enumerate(matches):
        heading = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        entries.append((heading, body[start:end], match.start()))
    return entries


def _line_facets(line: str) -> frozenset[str]:
    return frozenset(_FACET_TAG_RE.findall(line or ""))


def _rule_line_matches(line: str) -> bool:
    return _RULE_LINE_RE.match(line or "") is not None


def _entry_rule_lines(
    heading: str,
    body: str,
    *,
    facets: frozenset[str],
) -> list[str]:
    if _STANDING_RULE_HEADING in heading:
        return []
    kept: list[str] = []
    for line in body.splitlines():
        if not _rule_line_matches(line):
            continue
        line_facets = _line_facets(line)
        if not (line_facets & facets):
            continue
        kept.append(line.rstrip())
    return kept


def project_lessons_in_force(
    journal_text: str,
    *,
    role: str = "maestro",
    k: int = LESSONS_K,
) -> str:
    """Render ``## Lessons in force`` from full journal text."""
    facets = role_facet_set(role)
    if not (journal_text or "").strip():
        return "## Lessons in force\n\n(none)\n"
    projected: list[tuple[str, str]] = []
    for heading, body, _offset in _journal_entries(journal_text):
        for line in _entry_rule_lines(heading, body, facets=facets):
            projected.append((heading, line))
            if len(projected) >= k:
                break
        if len(projected) >= k:
            break
    if not projected:
        return "## Lessons in force\n\n(none)\n"
    blocks: list[str] = ["## Lessons in force", ""]
    for heading, line in projected:
        blocks.append(f"### {heading}")
        blocks.append(line)
        blocks.append("")
    return "\n".join(blocks).rstrip() + "\n"


def render_register_card(
    register_text: str | None,
    *,
    register_uri: str = MAESTRO_REGISTER_URI,
    max_lines: int = 8,
) -> str:
    """Render ``## Register`` — whole card capped at *max_lines*, or absence line."""
    if register_text is None:
        return (
            "## Register\n\n"
            f"Register card absent at {register_uri}.\n"
        )
    lines = [ln for ln in (register_text or "").splitlines() if ln.strip()]
    if not lines:
        return (
            "## Register\n\n"
            f"Register card absent at {register_uri}.\n"
        )
    capped = "\n".join(lines[:max_lines])
    return f"## Register\n\n{capped}\n"


def _read_cortex_uri(
    uri: str,
    *,
    read_uri: Callable[[str], str | None] | None = None,
) -> str | None:
    try:
        if read_uri is not None:
            return read_uri(uri)
        rel = uri.removeprefix("cortex://").lstrip("/")
        path = cortex_files_root() / rel
        if path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError:
        return None
    return None


def load_journal_text_for_hop(
    *,
    journal_text: str | None = None,
    read_uri: Callable[[str], str | None] | None = None,
    journal_uri: str = MAESTRO_JOURNAL_URI,
) -> tuple[str | None, str | None]:
    """Return ``(text, error_token)``; ``error_token`` is ``unavailable`` on read failure."""
    if journal_text is not None:
        return journal_text, None
    try:
        if read_uri is not None:
            loaded = read_uri(journal_uri)
            return loaded, None
        rel = journal_uri.removeprefix("cortex://").lstrip("/")
        path = cortex_files_root() / rel
        if not path.is_file():
            return None, None
        return path.read_text(encoding="utf-8"), None
    except OSError:
        return None, "unavailable"


def hop_journal_projection_fields(
    *,
    journal_text: str | None = None,
    register_text: str | None = None,
    read_uri: Callable[[str], str | None] | None = None,
    role: str = "maestro",
    inflight_projection_lookup: Callable[[], str | None] | None = None,
) -> dict[str, str]:
    """Lessons + register blocks for ``hop_successor_fields``."""
    text, err = load_journal_text_for_hop(journal_text=journal_text, read_uri=read_uri)
    inflight_line = ""
    if inflight_projection_lookup is not None:
        try:
            inflight_id = inflight_projection_lookup()
        except OSError:
            inflight_id = None
        if inflight_id:
            inflight_line = f"In-flight journal projection: {inflight_id}\n\n"

    if err == "unavailable":
        lessons = "## Lessons in force\n\njournal unavailable\n"
    elif text is None:
        lessons = project_lessons_in_force("", role=role)
    else:
        lessons = project_lessons_in_force(text, role=role)

    if inflight_line:
        prefix = "## Lessons in force\n\n"
        if lessons.startswith(prefix):
            tail = lessons[len(prefix) :]
            lessons = f"{prefix}{inflight_line}{tail}"
        else:
            lessons = lessons.rstrip() + "\n\n" + inflight_line

    if register_text is not None:
        register = render_register_card(register_text)
    else:
        try:
            reg_body = _read_cortex_uri(MAESTRO_REGISTER_URI, read_uri=read_uri)
        except OSError:
            reg_body = None
        register = render_register_card(reg_body)

    return {"lessons_in_force": lessons, "register_card": register}


__all__ = [
    "LESSONS_K",
    "MAESTRO_JOURNAL_URI",
    "MAESTRO_REGISTER_URI",
    "MAESTRO_ROLE_FACETS",
    "hop_journal_projection_fields",
    "load_journal_text_for_hop",
    "project_lessons_in_force",
    "render_register_card",
    "role_facet_set",
]
