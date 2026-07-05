"""Filesystem scan helpers for workspace stubs and cortex SOT bodies."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from _skill_constants import (
    _CORTEX_SOT_RE,
    _CREATE_SUPPRESSED_LIFECYCLES,
    _SKIP_CORTEX_SOT,
    _WS,
)
from _skill_related_parse import declared_related_skills, parse_frontmatter


def _cortex_files_root() -> Path:
    return Path(
        os.environ.get("CORTEX_FILES_ROOT", "/mnt/torus/mcp-data/files")
    ).expanduser()


def cortex_sot_slugs() -> set[str]:
    """Every ``*.md`` stem under cortex ``agent-skills/`` minus skipped names."""
    skills_dir = _cortex_files_root() / "agent-skills"
    if not skills_dir.is_dir():
        return set()
    return {
        path.stem
        for path in skills_dir.glob("*.md")
        if path.stem not in _SKIP_CORTEX_SOT
    }


def _scan_cortex_sot_metadata() -> dict[str, dict[str, object]]:
    """Declared frontmatter from cortex ``agent-skills/*.md`` (not workspace stubs)."""
    skills_dir = _cortex_files_root() / "agent-skills"
    if not skills_dir.is_dir():
        return {}
    found: dict[str, dict[str, object]] = {}
    for path in sorted(skills_dir.glob("*.md")):
        slug = path.stem
        if slug in _SKIP_CORTEX_SOT:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            print(f"ERROR: unreadable {path}", file=sys.stderr)
            continue
        fm = parse_frontmatter(text)
        entry: dict[str, object] = {}
        declared = declared_related_skills(text, fm)
        if isinstance(fm.get("related_skills"), list):
            entry["related_skills"] = declared
        elif declared:
            entry["related_skills"] = declared
        for key in ("trigger_match_terms", "trigger_short", "skill_category"):
            if key in fm:
                entry[key] = fm[key]
        if entry:
            found[slug] = entry
    return found


def _scan_cortex_sot_declared() -> dict[str, list[str]]:
    meta = _scan_cortex_sot_metadata()
    return {
        slug: list(meta["related_skills"])
        for slug, meta in meta.items()
        if isinstance(meta.get("related_skills"), list)
    }


def _scan_cortex_sot_skills() -> dict[str, dict[str, object]]:
    """Projection-ready rows for cortex ``agent-skills/*.md`` (not workspace stubs).

    Empty or missing frontmatter ``description:`` still yields a row — that state
    is SOT drift surfaced via ``_matches`` downstream, not a scan skip.
    """
    skills_dir = _cortex_files_root() / "agent-skills"
    if not skills_dir.is_dir():
        return {}
    found: dict[str, dict[str, object]] = {}
    for path in sorted(skills_dir.glob("*.md")):
        slug = path.stem
        if slug in _SKIP_CORTEX_SOT:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            print(f"ERROR: unreadable {path}", file=sys.stderr)
            continue
        fm = parse_frontmatter(text)
        description = str(fm.get("description") or "").strip()
        found[slug] = {
            "slug": slug,
            "frontmatter": fm,
            "description": description,
            "source_uri": f"agent-skills/{slug}.md",
            "related_skills": declared_related_skills(text, fm),
        }
    return found


def _create_lifecycle(fm: dict[str, object]) -> str:
    """Default discoverable lifecycle on CREATE when source frontmatter is non-suppressed."""
    raw = fm.get("lifecycle")
    if isinstance(raw, str):
        lc = raw.strip().lower()
        if lc in _CREATE_SUPPRESSED_LIFECYCLES:
            return lc
    return "active"


def _source_uri(slug: str, body: str, root: Path) -> str:
    sot = _CORTEX_SOT_RE.search(body)
    if sot:
        return f"agent-skills/{sot.group(1)}.md"
    return f"{_WS}/.cursor/skills/{slug}/SKILL.md"


def _scan_skills(root: Path) -> dict[str, dict[str, object]]:
    skills_dir = root / ".cursor" / "skills"
    if not skills_dir.is_dir():
        print(f"ERROR: missing skills dir: {skills_dir}", file=sys.stderr)
        return {}
    found: dict[str, dict[str, object]] = {}
    for skill_path in sorted(skills_dir.glob("*/SKILL.md")):
        slug = skill_path.parent.name
        try:
            text = skill_path.read_text(encoding="utf-8")
        except OSError:
            print(f"ERROR: unreadable {skill_path}", file=sys.stderr)
            continue
        fm = parse_frontmatter(text)
        description = str(fm.get("description") or "").strip()
        if not description:
            print(f"ERROR: missing description: {skill_path}", file=sys.stderr)
            continue
        found[slug] = {
            "slug": slug,
            "frontmatter": fm,
            "description": description,
            "source_uri": _source_uri(slug, text, root),
            "related_skills": declared_related_skills(text, fm),
        }
    return found
