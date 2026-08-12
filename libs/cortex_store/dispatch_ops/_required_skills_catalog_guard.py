"""Reject uncatalogued ``required_skills`` slugs at write time (arc 7098 P1).

Materialize already fail-louds via ``get_skill_catalog().get``
(``SkillCatalogResolveError``). That is too late — densify can stamp
rule-only stems (``skill-surface``, ``testing-discipline``,
``capability-dispatch``, …) into attrs and only discover the poison at fire.

Authority: ``config/skills.yaml`` via ``get_skill_catalog`` — same set as
implement materialize (a:25376). Do **not** add rule stems to the catalog
to paper over this (rule ≠ skill; a:23622).

Fail loud (422). No silent strip.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

_ERROR_CODE = "required_skills_uncatalogued"


def _bare_slug(entry: str) -> str:
    """Normalize ``agent_skill:`` / ``rule:`` / typed prefixes to a bare key."""
    raw = entry.strip()
    if raw.startswith("agent_skill:"):
        return raw.removeprefix("agent_skill:")
    if raw.startswith("rule:"):
        return raw.removeprefix("rule:")
    if ":" in raw and not raw.startswith("workspaces://"):
        return raw.split(":", 1)[1]
    return raw


def reject_uncatalogued_required_skills(skills: Any) -> None:
    """Raise 422 when any ``required_skills`` entry is absent from the catalog.

    Non-list values are ignored here — ``validate_distilled_attributes`` already
    owns implement-lane shape rejects for non-list / empty lists.
    """
    if not isinstance(skills, list):
        return
    from claude_bundles.catalog import get_skill_catalog

    catalog = get_skill_catalog()
    missing: list[str] = []
    for entry in skills:
        if not isinstance(entry, str) or not entry.strip():
            continue
        bare = _bare_slug(entry)
        try:
            catalog.get(bare)
        except KeyError:
            missing.append(bare)
    if not missing:
        return
    # Dedup while preserving order (first-seen wins for the error payload).
    seen: set[str] = set()
    ordered: list[str] = []
    for slug in missing:
        if slug in seen:
            continue
        seen.add(slug)
        ordered.append(slug)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "error": _ERROR_CODE,
            "attribute": "required_skills",
            "rejected_slugs": ordered,
            "message": (
                "required_skills entries must be catalog-registered skill slugs "
                "(config/skills.yaml / get_skill_catalog). Rule-only / *_ulg.mdc "
                "stems (e.g. skill-surface, testing-discipline, capability-dispatch) "
                "are invalid — they are Cursor rules, not agent_skill bodies "
                "(a:23622; arc 7098 P1). Do not add rule stems to the skill catalog."
            ),
        },
    )


__all__ = [
    "reject_uncatalogued_required_skills",
    "_ERROR_CODE",
    "_bare_slug",
]
