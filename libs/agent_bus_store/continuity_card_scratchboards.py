"""Parse continuity-card ``## Scratchboards`` and ``## Skills`` sections.

Living arc scratchpads (frictions, stall playbooks, session notes) sit on
``cortex://notes/system/scratchboards/`` and are named from the continuity card.
``## Skills`` slug rows are staged into the resume pour as ``skills_to_use``.
Resume fence and lint import this module — do not re-parse the sections elsewhere.
"""

from __future__ import annotations

import re
from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root

_SCRATCHBOARDS_HEADING = "## Scratchboards"
_SKILLS_HEADING = "## Skills"
_CORTEX_URI_RE = re.compile(r"cortex://[^\s)\]>`]+")
_BACKTICK_SLUG_RE = re.compile(r"`/?([A-Za-z0-9][A-Za-z0-9._-]*)`")
_BARE_SLUG_RE = re.compile(r"^/?([A-Za-z0-9][A-Za-z0-9._-]*)")
_TABLE_RULE_RE = re.compile(r"^\|?[\s\-:|]+\|?$")
_TABLE_HEADER_CELLS = frozenset({"slug", "skill", "skills", "name"})
_REQUIRED_HEADINGS: tuple[str, ...] = (
    "## Skills",
    "## Stance",
    "## Why this house",
    "## Objective",
    "## Runbooks",
    "## Rules",
    "## Sidecars",
    "## Scratchboards",
    "## House",
)


def scratchboard_relpath(thread_id: str, slug: str) -> str:
    """Default scratchboard path for a continuity root."""
    normalized = thread_id.strip().removeprefix("agent-bus:")
    stem = slug.strip().removesuffix(".md").removesuffix("-scratchboard")
    return f"notes/system/scratchboards/{normalized}-{stem}-scratchboard.md"


def scratchboard_uri(thread_id: str, slug: str) -> str:
    """Canonical cortex URI for a thread-scoped scratchboard."""
    return f"cortex://{scratchboard_relpath(thread_id, slug)}"


def extract_scratchboard_uris(card_text: str) -> list[str]:
    """Return sorted unique ``cortex://`` URIs under ``## Scratchboards``."""
    if _SCRATCHBOARDS_HEADING not in card_text:
        return []
    chunk = card_text.split(_SCRATCHBOARDS_HEADING, 1)[1].split("## ", 1)[0]
    return sorted(set(_CORTEX_URI_RE.findall(chunk)))


def _normalize_skill_slug(token: str) -> str | None:
    """Strip backticks, leading ``/``, and parenthetical suffixes.

    Trailing junk after the slug (spaces, prose) is treated as malformed and
    skipped — same fail-open posture as a bad table row.
    """
    cleaned = token.strip().strip("`").strip()
    cleaned = cleaned.split("(", 1)[0].strip().lstrip("/").strip()
    match = _BARE_SLUG_RE.match(cleaned)
    if not match or cleaned[match.end() :].strip():
        return None
    return match.group(1)


def extract_card_skills(card_text: str) -> list[str]:
    """Return skill slugs under ``## Skills`` in card order.

    Accepts bullet and table rows. Leading ``/`` and ``(fallback …)`` suffixes
    are stripped the same way ``merge_house_pool_skills`` normalizes pool
    ``must_load`` cells. Missing or empty sections yield ``[]`` — never raise.
    Malformed rows are skipped.
    """
    if not card_text or _SKILLS_HEADING not in card_text:
        return []
    chunk = card_text.split(_SKILLS_HEADING, 1)[1].split("## ", 1)[0]
    ordered: list[str] = []
    seen: set[str] = set()
    for raw_line in chunk.splitlines():
        line = raw_line.strip()
        if not line or _TABLE_RULE_RE.fullmatch(line):
            continue
        candidate: str | None = None
        backtick = _BACKTICK_SLUG_RE.search(line)
        if backtick:
            candidate = backtick.group(1)
        elif line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if not cells or cells[0].lower() in _TABLE_HEADER_CELLS:
                continue
            candidate = _normalize_skill_slug(cells[0])
        elif line[:1] in "-*":
            body = line[1:].strip()
            for sep in (" — ", " – ", " - "):
                if sep in body:
                    body = body.split(sep, 1)[0].strip()
                    break
            nested = _BACKTICK_SLUG_RE.search(body)
            candidate = nested.group(1) if nested else _normalize_skill_slug(body)
        if not candidate:
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(candidate)
    return ordered


def missing_required_headings(card_text: str) -> list[str]:
    """Headings required on every continuity card (``card-schema.md``)."""
    return [heading for heading in _REQUIRED_HEADINGS if heading not in card_text]


def validate_continuity_card(card_text: str) -> list[str]:
    """Return human-readable defects; empty list means shape ok."""
    errors: list[str] = []
    for heading in missing_required_headings(card_text):
        errors.append(f"missing_heading:{heading.removeprefix('## ').lower()}")
    if _SCRATCHBOARDS_HEADING in card_text:
        body = card_text.split(_SCRATCHBOARDS_HEADING, 1)[1].split("## ", 1)[0].strip()
        if not body:
            errors.append("empty_scratchboards_section")
    if _SKILLS_HEADING in card_text:
        body = card_text.split(_SKILLS_HEADING, 1)[1].split("## ", 1)[0].strip()
        if not body:
            errors.append("empty_skills_section")
    return errors


def scratchboard_path_from_uri(uri: str) -> Path | None:
    """Map ``cortex://notes/system/scratchboards/…`` to on-disk path."""
    prefix = "cortex://notes/system/scratchboards/"
    if not uri.startswith(prefix):
        return None
    rel = uri.removeprefix("cortex://")
    path = cortex_files_root() / rel
    return path if path.parent.is_dir() or path.is_file() else None


__all__ = [
    "extract_card_skills",
    "extract_scratchboard_uris",
    "missing_required_headings",
    "scratchboard_path_from_uri",
    "scratchboard_relpath",
    "scratchboard_uri",
    "validate_continuity_card",
]
