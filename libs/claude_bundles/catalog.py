"""Typed loader for ``config/skills.yaml`` — sole skill-placement authority."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

import yaml

SurfaceClass = Literal["cursor_only", "shared_sync", "life_local"]
McpSurface = Literal["none", "life", "code"]

_SURFACE_CLASSES: Final[frozenset[str]] = frozenset(
    {"cursor_only", "shared_sync", "life_local"}
)
_MCP_SURFACES: Final[frozenset[str]] = frozenset({"none", "life", "code"})
_CLAUDE_AI_CLASSES: Final[frozenset[str]] = frozenset({"shared_sync", "life_local"})

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CATALOG = _REPO_ROOT / "config" / "skills.yaml"
_WS = "workspaces://universal-llm-gateway"


@dataclass(frozen=True, slots=True)
class SkillCatalogEntry:
    """One authoritative placement row."""

    slug: str
    surface_class: SurfaceClass
    mcp_surface_required: McpSurface
    aliases: tuple[str, ...] = ()
    delivery_priority: int | None = None
    sot_dirname: str | None = None


@dataclass(frozen=True, slots=True)
class SkillCatalog:
    """Validated skill-placement catalog with derived query views."""

    entries: dict[str, SkillCatalogEntry]
    alias_to_canonical: dict[str, str]

    def get(self, slug: str) -> SkillCatalogEntry:
        key = self.canonical_slug(slug)
        try:
            return self.entries[key]
        except KeyError as exc:
            raise KeyError(f"slug {slug!r} absent from skill catalog") from exc

    def canonical_slug(self, slug_or_id: str) -> str:
        raw = slug_or_id.strip()
        if raw.startswith("agent_skill:"):
            raw = raw.removeprefix("agent_skill:")
        elif raw.startswith("rule:"):
            raw = raw.removeprefix("rule:")
        elif ":" in raw and not raw.startswith("workspaces://"):
            raw = raw.split(":", 1)[1]
        return self.alias_to_canonical.get(raw, raw)

    def surface_class_for(self, slug: str) -> SurfaceClass:
        return self.get(slug).surface_class

    def mcp_surface_required_for(self, slug: str) -> McpSurface:
        return self.get(slug).mcp_surface_required

    def requires_mcp(self, slug: str) -> bool:
        return self.mcp_surface_required_for(slug) != "none"

    def slugs(self, *, surface_class: SurfaceClass | None = None) -> list[str]:
        if surface_class is None:
            return sorted(self.entries)
        return sorted(
            slug
            for slug, entry in self.entries.items()
            if entry.surface_class == surface_class
        )

    def shared_sync_slugs(self) -> list[str]:
        return self.slugs(surface_class="shared_sync")

    def life_local_slugs(self) -> list[str]:
        return self.slugs(surface_class="life_local")

    def cursor_only_slugs(self) -> list[str]:
        return self.slugs(surface_class="cursor_only")

    def cursor_indexed_slugs(self) -> list[str]:
        return sorted(
            slug
            for slug, entry in self.entries.items()
            if entry.surface_class in ("shared_sync", "cursor_only")
        )

    def claude_ai_targets(self) -> list[str]:
        """Desired Customize → Skills set (shared_sync ∪ life_local)."""
        return sorted(
            slug
            for slug, entry in self.entries.items()
            if entry.surface_class in _CLAUDE_AI_CLASSES
        )

    def source_uri_for(self, slug: str) -> str:
        entry = self.get(slug)
        dirname = entry.sot_dirname or entry.slug
        if entry.surface_class == "life_local":
            return f"{_WS}/.claude/skills/{dirname}/SKILL.md"
        return f"{_WS}/.cursor/skills/{dirname}/SKILL.md"

    def resolve_sot(self, slug: str, repo_root: Path) -> tuple[Path, str]:
        entry = self.get(slug)
        dirname = entry.sot_dirname or entry.slug
        plugin = (
            repo_root
            / "cursor-plugins"
            / "ulg-ecosystem"
            / "skills"
            / dirname
            / "SKILL.md"
        )
        if plugin.is_file():
            return plugin, "cursor-plugins/ulg-ecosystem/skills"
        if entry.surface_class == "life_local":
            path = repo_root / ".claude" / "skills" / dirname / "SKILL.md"
            label = ".claude/skills"
        else:
            path = repo_root / ".cursor" / "skills" / dirname / "SKILL.md"
            label = ".cursor/skills"
        if path.is_file():
            return path, label
        raise FileNotFoundError(f"no SOT for {entry.slug!r} — searched: {path}")

    def delivery_priority_for(self, slug: str) -> int | None:
        return self.get(slug).delivery_priority


class CatalogValidationError(ValueError):
    """Fail-loud catalog invariant violation."""


def _parse_entry(slug: str, raw: object) -> SkillCatalogEntry:
    if not isinstance(raw, dict):
        raise CatalogValidationError(f"{slug}: row must be a mapping")
    surface = raw.get("surface_class")
    mcp = raw.get("mcp_surface_required")
    if surface not in _SURFACE_CLASSES:
        raise CatalogValidationError(
            f"{slug}: illegal surface_class {surface!r}; "
            f"expected one of {sorted(_SURFACE_CLASSES)}"
        )
    if mcp not in _MCP_SURFACES:
        raise CatalogValidationError(
            f"{slug}: illegal mcp_surface_required {mcp!r}; "
            f"expected one of {sorted(_MCP_SURFACES)}"
        )
    if surface in _CLAUDE_AI_CLASSES and mcp == "code":
        raise CatalogValidationError(
            f"{slug}: Claude.ai target (surface_class={surface}) "
            "cannot require mcp_surface_required=code"
        )
    aliases_raw = raw.get("aliases") or []
    if not isinstance(aliases_raw, list):
        raise CatalogValidationError(f"{slug}: aliases must be a list")
    aliases = tuple(str(a).strip() for a in aliases_raw if str(a).strip())
    priority = raw.get("delivery_priority")
    if priority is not None and not isinstance(priority, int):
        raise CatalogValidationError(f"{slug}: delivery_priority must be int")
    sot_dirname = raw.get("sot_dirname")
    if sot_dirname is not None and (
        not isinstance(sot_dirname, str) or not sot_dirname.strip()
    ):
        raise CatalogValidationError(f"{slug}: sot_dirname must be a non-empty string")
    return SkillCatalogEntry(
        slug=slug,
        surface_class=surface,  # type: ignore[arg-type]
        mcp_surface_required=mcp,  # type: ignore[arg-type]
        aliases=aliases,
        delivery_priority=priority,
        sot_dirname=str(sot_dirname).strip() if sot_dirname else None,
    )


def _plugin_census_slugs(repo_root: Path) -> set[str]:
    """Slugs shipped via ulg-ecosystem plugin (Cursor discovery SoT)."""
    census = repo_root / "cursor-plugins" / "ulg-ecosystem" / "SKILLS_CENSUS.txt"
    if not census.is_file():
        return set()
    out: set[str] = set()
    for line in census.read_text(encoding="utf-8").splitlines():
        slug = line.split("#", 1)[0].strip()
        if not slug:
            continue
        skill = (
            repo_root
            / "cursor-plugins"
            / "ulg-ecosystem"
            / "skills"
            / slug
            / "SKILL.md"
        )
        if skill.is_file():
            out.add(slug)
    return out


def _active_sot_slugs(repo_root: Path) -> set[str]:
    cursor = {
        path.parent.name
        for path in (repo_root / ".cursor" / "skills").glob("*/SKILL.md")
        if path.parent.name != "README"
    }
    # Plugin census is Cursor-indexed via the installed plugin, not .cursor/skills.
    return cursor | _plugin_census_slugs(repo_root)


def _validate_sot_coverage(catalog: SkillCatalog, repo_root: Path) -> None:
    """Every active Cursor SOT and every life_local row must resolve exactly once."""
    errors: list[str] = []
    cursor_sots = _active_sot_slugs(repo_root)
    catalog_dirs = {
        (entry.sot_dirname or entry.slug)
        for entry in catalog.entries.values()
        if entry.surface_class in ("shared_sync", "cursor_only")
    }
    missing_rows = sorted(cursor_sots - catalog_dirs)
    if missing_rows:
        errors.append(f"active Cursor SOTs missing catalog rows: {missing_rows}")
    extra_rows = sorted(catalog_dirs - cursor_sots)
    if extra_rows:
        errors.append(f"catalog cursor rows without Cursor SOT: {extra_rows}")

    for slug in catalog.life_local_slugs():
        entry = catalog.entries[slug]
        dirname = entry.sot_dirname or slug
        path = repo_root / ".claude" / "skills" / dirname / "SKILL.md"
        if not path.is_file():
            errors.append(f"life_local {slug!r} missing SOT at {path}")
        cursor_path = repo_root / ".cursor" / "skills" / dirname / "SKILL.md"
        if cursor_path.is_file():
            errors.append(
                f"life_local {slug!r} must not also have Cursor SOT {cursor_path}"
            )

    for slug, entry in catalog.entries.items():
        if entry.surface_class == "life_local":
            continue
        try:
            catalog.resolve_sot(slug, repo_root)
        except FileNotFoundError as exc:
            errors.append(str(exc))

    if errors:
        raise CatalogValidationError("; ".join(errors))


def load_skill_catalog(
    path: Path | None = None,
    *,
    repo_root: Path | None = None,
    validate_sot: bool = True,
) -> SkillCatalog:
    """Load and validate the skill catalog (fail-loud)."""
    catalog_path = path or _DEFAULT_CATALOG
    root = repo_root or _REPO_ROOT
    if not catalog_path.is_file():
        raise CatalogValidationError(f"skill catalog missing: {catalog_path}")
    raw = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    skills_raw = raw.get("skills")
    if not isinstance(skills_raw, dict) or not skills_raw:
        raise CatalogValidationError("config/skills.yaml: skills mapping required")

    entries: dict[str, SkillCatalogEntry] = {}
    alias_to_canonical: dict[str, str] = {}
    for slug, row in skills_raw.items():
        key = str(slug).strip()
        if not key:
            raise CatalogValidationError("empty skill slug")
        if key in entries:
            raise CatalogValidationError(f"duplicate slug {key!r}")
        entry = _parse_entry(key, row)
        entries[key] = entry
        for alias in entry.aliases:
            if alias in entries or alias in alias_to_canonical:
                raise CatalogValidationError(
                    f"alias {alias!r} for {key!r} collides with another slug/alias"
                )
            alias_to_canonical[alias] = key

    catalog = SkillCatalog(entries=entries, alias_to_canonical=alias_to_canonical)
    if validate_sot:
        _validate_sot_coverage(catalog, root)
    return catalog


@lru_cache(maxsize=1)
def get_skill_catalog() -> SkillCatalog:
    """Process-wide cached catalog (repo default path)."""
    return load_skill_catalog()


def clear_skill_catalog_cache() -> None:
    """Test helper — drop the cached catalog."""
    get_skill_catalog.cache_clear()
