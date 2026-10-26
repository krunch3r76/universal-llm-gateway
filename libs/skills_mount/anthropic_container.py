"""Anthropic Messages API ``container.skills`` mount resolution."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from agent_seat.guidance_entity import entity_slug_from_id
from implement_admission.skill_catalog_resolver import canonical_agent_skill_id

from skills_mount.resolve import SkillMountResolveError

MAX_ANTHROPIC_CONTAINER_SKILLS = 8

SkillKind = Literal["custom", "anthropic"]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_INDEX_PATH = _PROJECT_ROOT / "config" / "anthropic_skills_index.json"


class AnthropicContainerSkillError(SkillMountResolveError):
    """Raised when a slug lacks a container index mapping."""


@lru_cache(maxsize=1)
def _load_index_raw() -> dict[str, Any]:
    override = os.environ.get("ANTHROPIC_SKILLS_INDEX_PATH", "").strip()
    path = Path(override) if override else _DEFAULT_INDEX_PATH
    if not path.is_file():
        return {"skills": {}}
    with path.open(encoding="utf-8") as fh:
        loaded = json.load(fh)
    return loaded if isinstance(loaded, dict) else {"skills": {}}


def anthropic_skill_index_entry(slug: str) -> dict[str, Any] | None:
    """Return index row for *slug* (canonical slug or entity id), or ``None``."""
    canonical = canonical_agent_skill_id(slug)
    skills = _load_index_raw().get("skills") or {}
    if not isinstance(skills, dict):
        return None
    for key, row in skills.items():
        if not isinstance(row, dict):
            continue
        key_canonical = canonical_agent_skill_id(str(key))
        if key_canonical == canonical or str(key) == slug:
            return row
        mapped_slug = row.get("slug")
        if mapped_slug and canonical_agent_skill_id(str(mapped_slug)) == canonical:
            return row
    return None


def _layer_c_without_container_index(slug: str) -> bool:
    """True for unindexed scope-default ``rule:*`` ids (Layer-C inline only)."""
    canonical = canonical_agent_skill_id(slug)
    return canonical.startswith("rule:") or slug.startswith("rule:")


def partition_slugs_for_container(
    slugs: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split ordered *slugs* into (container_mount, inline_overflow).

    The first ``MAX_ANTHROPIC_CONTAINER_SKILLS`` slots mount when indexed; an
    unindexed caller/agent_skill in those slots fails closed. Index >= 8 and
    unindexed ``rule:*`` scope defaults overflow to Layer-C inline.
    """
    mount: list[str] = []
    inline: list[str] = []
    for idx, slug in enumerate(slugs):
        if idx >= MAX_ANTHROPIC_CONTAINER_SKILLS:
            inline.append(slug)
            continue
        if anthropic_skill_index_entry(slug):
            mount.append(slug)
        elif _layer_c_without_container_index(slug):
            inline.append(slug)
        else:
            raise AnthropicContainerSkillError(
                f"anthropic_container skill not indexed: {slug!r}"
            )
    return tuple(mount), tuple(inline)


def resolve_container_skill_entries(
    slugs: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Build Anthropic ``container.skills`` entries for mapped *slugs*."""
    entries: list[dict[str, Any]] = []
    for slug in slugs:
        row = anthropic_skill_index_entry(slug)
        if not row:
            raise AnthropicContainerSkillError(
                f"anthropic_container skill not indexed: {slug!r}"
            )
        skill_id = str(row.get("skill_id") or "").strip()
        if not skill_id:
            raise AnthropicContainerSkillError(
                f"anthropic_container skill_id missing for slug={slug!r}"
            )
        kind_raw = str(row.get("type") or "custom").strip().lower()
        kind: SkillKind = "anthropic" if kind_raw == "anthropic" else "custom"
        version = str(row.get("version") or "latest")
        entries.append(
            {
                "type": kind,
                "skill_id": skill_id,
                "version": version,
                "slug": entity_slug_from_id(canonical_agent_skill_id(slug))
                if ":" in slug
                else slug,
            }
        )
    return entries


def clear_anthropic_skills_index_cache_for_tests() -> None:
    _load_index_raw.cache_clear()
