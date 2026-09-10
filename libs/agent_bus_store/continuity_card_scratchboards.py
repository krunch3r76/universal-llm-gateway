"""Parse continuity-card ``## Scratchboards`` pointers.

Living arc scratchpads (frictions, stall playbooks, session notes) sit on
``cortex://notes/system/scratchboards/`` and are named from the continuity card.
Resume fence and lint import this module — do not re-parse the section elsewhere.
"""

from __future__ import annotations

import re
from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root

_SCRATCHBOARDS_HEADING = "## Scratchboards"
_CORTEX_URI_RE = re.compile(r"cortex://[^\s)\]>`]+")
_REQUIRED_HEADINGS: tuple[str, ...] = (
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
    "extract_scratchboard_uris",
    "missing_required_headings",
    "scratchboard_path_from_uri",
    "scratchboard_relpath",
    "scratchboard_uri",
    "validate_continuity_card",
]
