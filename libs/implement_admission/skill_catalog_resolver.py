"""Catalog-backed skill slug → source_uri resolution.

Authority: ``config/skills.yaml`` via ``claude_bundles.catalog``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Final

from claude_bundles.catalog import get_skill_catalog

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WS = "workspaces://universal-llm-gateway"
_CATALOG_PATH = _REPO_ROOT / "config" / "skills.yaml"

RESOLVER_VERSION: Final[str] = "catalog"


class SkillCatalogResolveError(LookupError):
    """Canonical slug absent from the skill catalog."""


# Compat alias — prior call sites / packets named SkillSourceResolveError.
SkillSourceResolveError = SkillCatalogResolveError


def catalog_digest() -> str:
    """SHA-256 of ``config/skills.yaml`` for freshness probes."""
    return "sha256:" + hashlib.sha256(_CATALOG_PATH.read_bytes()).hexdigest()


def catalog_source_uris(*, repo_root: Path | None = None) -> dict[str, str]:
    """Resolved workspace URIs for every catalog slug (plugin-aware)."""
    root = (repo_root or _REPO_ROOT).resolve()
    catalog = get_skill_catalog()
    uris: dict[str, str] = {}
    for slug in sorted(catalog.entries):
        path, _ = catalog.resolve_sot(slug, root)
        rel = path.relative_to(root).as_posix()
        uris[slug] = f"{_WS}/{rel}"
    return uris


def canonical_catalog_slug(slug_or_entity_id: str) -> str:
    """Normalize any entity id or bare slug to a canonical catalog key."""
    return get_skill_catalog().canonical_slug(slug_or_entity_id)


def canonical_agent_skill_id(slug_or_entity_id: str) -> str:
    """Double-load exclusion key — always ``agent_skill:{canonical_slug}``."""
    return f"agent_skill:{canonical_catalog_slug(slug_or_entity_id)}"


def resolve_canonical_source_uri(slug_or_entity_id: str) -> str:
    """Map slug/entity id → workspace ``source_uri`` via the skill catalog."""
    catalog = get_skill_catalog()
    key = catalog.canonical_slug(slug_or_entity_id)
    try:
        catalog.get(key)
    except KeyError as exc:
        raise SkillCatalogResolveError(
            f"canonical slug {key!r} absent from skill catalog"
        ) from exc
    path, _ = catalog.resolve_sot(key, _REPO_ROOT)
    rel = path.relative_to(_REPO_ROOT).as_posix()
    return f"{_WS}/{rel}"


def catalog_uri_bytes_for_digest() -> bytes:
    """Stable serialization of catalog-resolved URIs (freshness / drift probes)."""
    return json.dumps(
        catalog_source_uris(), sort_keys=True, separators=(",", ":")
    ).encode()
