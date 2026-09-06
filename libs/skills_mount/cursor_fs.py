"""Resolve canonical skill slugs to SKILL.md paths a Cursor seat can discover.

Sibling of :mod:`skills_mount.resolve`, which renders bundles as base64 zips for
provider APIs that have no filesystem. Cursor is the opposite case: ``cursor-agent``
discovers skills *by reading directories*, so the mountable form of a skill here is
a readable path, not a payload.

Three source layers, in precedence order:

1. ``cursor-plugins/ulg-ecosystem/skills/{slug}/SKILL.md`` — ecosystem plugin census,
   already copied into every dispatch HOME by :mod:`cursor_home`.
2. ``.cursor/skills/{slug}/SKILL.md`` — workspace-local skills, live from the cwd.
3. ``.claude/skills/{slug}/SKILL.md`` — ``surface_class: life_local`` bodies.

Layer 3 is the one that needs explaining. ``life_local`` slugs (``prose-discipline``,
``outbound-voice-spec``, …) are deliberately absent from both Cursor layers:
``config/skills.yaml`` routes them to claude.ai Customize, and
``catalog._validate_sot_coverage`` *forbids* them a ``.cursor/skills`` body. ``.claude``
is also gitignored, so the tree exists only in the hub checkout — never in a Lane-B
worktree. The net effect before this module existed: a cursor-sdk seat told to
``Use the prose-discipline skill`` had nothing to resolve on any path it could see,
and failed silently.

Reading layer 3 from the hub keeps that doctrine intact. Nothing is promoted to a
Cursor SoT and nothing enters a tracked tree; the caller stages a copy into an
ephemeral per-dispatch HOME, which :func:`cursor_home.prune_stale_dispatch_homes`
reaps. ``repo_root`` must therefore be the hub source repo, not a worktree.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from claude_bundles.catalog import SkillCatalog, load_skill_catalog

PLUGIN_SKILLS_RELPATH = Path("cursor-plugins") / "ulg-ecosystem" / "skills"
CURSOR_SKILLS_RELPATH = Path(".cursor") / "skills"
CLAUDE_SKILLS_RELPATH = Path(".claude") / "skills"

#: Layer label carried on each resolved row, ordered by the precedence above.
SotLayer = str


class CursorSkillSotError(LookupError):
    """No Cursor-discoverable SKILL.md for a requested slug."""


@lru_cache(maxsize=1)
def _catalog() -> SkillCatalog:
    """Catalog rows without SoT-coverage validation.

    ``get_skill_catalog`` validates coverage against its own package repo root,
    which raises from any Lane-B worktree: ``.claude/`` and the personal
    ``.cursor/skills`` dirs are gitignored, so a worktree legitimately has neither.
    Membership and alias resolution do not depend on that check, and per-slug
    path resolution below is the real coverage test for the slugs a caller asked
    about. Same escape hatch ``resolve.resolve_skill_bundles`` already takes.
    """
    return load_skill_catalog(validate_sot=False)


@dataclass(frozen=True, slots=True)
class CursorSkillSot:
    """One slug resolved to a readable SKILL.md on this host."""

    requested_id: str
    canonical_slug: str
    path: Path
    layer: SotLayer


@dataclass(frozen=True, slots=True)
class CursorSkillResolution:
    """Split of a requested slug list into resolvable and unresolvable rows."""

    resolved: tuple[CursorSkillSot, ...]
    unresolved: tuple[tuple[str, str], ...]

    @property
    def unresolved_slugs(self) -> tuple[str, ...]:
        return tuple(slug for slug, _reason in self.unresolved)


def _candidate_paths(slug: str, repo_root: Path) -> tuple[tuple[Path, SotLayer], ...]:
    return (
        (repo_root / PLUGIN_SKILLS_RELPATH / slug / "SKILL.md", "plugin"),
        (repo_root / CURSOR_SKILLS_RELPATH / slug / "SKILL.md", "workspace"),
        (repo_root / CLAUDE_SKILLS_RELPATH / slug / "SKILL.md", "life_local"),
    )


def resolve_cursor_skill_sot(
    slug_or_entity_id: str,
    *,
    repo_root: Path,
) -> CursorSkillSot:
    """Return the highest-precedence readable SKILL.md for *slug_or_entity_id*.

    Args:
        slug_or_entity_id: Bare slug or ``agent_skill:``/``rule:``-qualified id.
        repo_root: Hub source repo. A worktree root cannot resolve ``life_local``
            slugs because ``.claude`` is gitignored.

    Returns:
        The resolved row, carrying which layer answered.

    Raises:
        CursorSkillSotError: The slug is absent from the catalog, or carded but
            with no readable body under any of the three layers.
    """
    requested = str(slug_or_entity_id or "").strip()
    if not requested:
        raise CursorSkillSotError("empty skill id in skills= list")
    catalog = _catalog()
    canonical = catalog.canonical_slug(requested)
    try:
        catalog.get(canonical)
    except KeyError as exc:
        raise CursorSkillSotError(
            f"skill id {requested!r} absent from skill catalog"
        ) from exc

    for path, layer in _candidate_paths(canonical, repo_root):
        if path.is_file():
            return CursorSkillSot(
                requested_id=requested,
                canonical_slug=canonical,
                path=path,
                layer=layer,
            )

    searched = ", ".join(
        str(path) for path, _layer in _candidate_paths(canonical, repo_root)
    )
    raise CursorSkillSotError(
        f"skill id {requested!r}: no Cursor-discoverable SKILL.md — searched {searched}"
    )


def classify_cursor_skills(
    slugs: list[str] | tuple[str, ...] | None,
    *,
    repo_root: Path,
) -> CursorSkillResolution:
    """Resolve every slug, collecting failures instead of raising on the first.

    Canonical duplicates collapse to their first requested spelling, matching
    ``agent_seat.skills_merge.resolve_effective_skills``.
    """
    resolved: list[CursorSkillSot] = []
    unresolved: list[tuple[str, str]] = []
    seen: set[str] = set()

    for raw in slugs or ():
        requested = str(raw or "").strip()
        if not requested:
            continue
        try:
            row = resolve_cursor_skill_sot(requested, repo_root=repo_root)
        except CursorSkillSotError as exc:
            unresolved.append((requested, str(exc)))
            continue
        if row.canonical_slug in seen:
            continue
        seen.add(row.canonical_slug)
        resolved.append(row)

    return CursorSkillResolution(resolved=tuple(resolved), unresolved=tuple(unresolved))
