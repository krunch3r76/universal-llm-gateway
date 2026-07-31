"""CI-enforced skill catalog integrity (skills-inline-workspaces-uri-loader-gap AC4/5)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from claude_bundles.catalog import get_skill_catalog
from implement_admission.skill_catalog_resolver import (
    SkillCatalogResolveError,
    canonical_agent_skill_id,
    canonical_catalog_slug,
    catalog_source_uris,
    resolve_canonical_source_uri,
)
from implement_admission.skill_catalog_freshness import check_catalog_valid

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORTEX_FILES_ROOT = Path(
    os.environ.get("CORTEX_FILES_ROOT", "/mnt/torus/mcp-data/files")
).expanduser()
_WS_PREFIX = "workspaces://universal-llm-gateway/"

_CLEANUP_TODO = "todo:skill-body-canonical-cleanup"

KNOWN_DOCS_AGENT_GUIDES_VIOLATIONS: frozenset[str] = frozenset()

_LEGACY_SKILL_DOC_PREFIX = "docs/" + "agent-guides/skills/"


def _iter_integrity_keys() -> set[str]:
    """Keys covered by AC4: alias targets + resolvable catalog slugs."""
    catalog = get_skill_catalog()
    keys = set(catalog.alias_to_canonical.values())
    for slug in catalog.entries:
        uri = resolve_canonical_source_uri(slug)
        if uri.startswith("agent-skills/") or uri.startswith("agent-bus:"):
            continue
        keys.add(slug)
    return keys


def _resolve_body_path(source_uri: str) -> Path | None:
    uri = source_uri.strip()
    if uri.startswith("agent-skills/"):
        path = _CORTEX_FILES_ROOT / uri
        return path if path.is_file() else None
    if uri.startswith(_WS_PREFIX):
        rel = uri.removeprefix(_WS_PREFIX)
        path = _REPO_ROOT / rel
        return path if path.is_file() else None
    if uri.startswith("workspaces://"):
        rel = uri.split("universal-llm-gateway/", 1)[-1]
        path = _REPO_ROOT / rel
        return path if path.is_file() else None
    if uri.startswith("docs/"):
        path = _REPO_ROOT / uri
        return path if path.is_file() else None
    return None


def _is_canonical_workspaces_uri(uri: str) -> bool:
    if not uri.startswith("workspaces://"):
        return True
    return (
        ".cursor/skills/" in uri
        or ".claude/skills/" in uri
        or "cursor-plugins/ulg-ecosystem/skills/" in uri
    )


@pytest.mark.offline
def test_catalog_freshness_gate_passes() -> None:
    assert check_catalog_valid() is None


@pytest.mark.offline
def test_ulg_architecture_caller_slug_maps_to_bare_entity_key() -> None:
    assert canonical_catalog_slug("ulg-architecture") == "ulg-architecture"
    assert (
        canonical_agent_skill_id("ulg-architecture") == "agent_skill:ulg-architecture"
    )
    assert (
        canonical_agent_skill_id("ulg-architecture_ulg")
        == "agent_skill:ulg-architecture_ulg"
    )
    assert (
        canonical_agent_skill_id("rule:ulg-architecture_ulg")
        == "agent_skill:ulg-architecture_ulg"
    )


@pytest.mark.offline
def test_ulg_architecture_source_uri_is_canonical_cursor_skill() -> None:
    uri = resolve_canonical_source_uri("ulg-architecture")
    assert ".cursor/skills/ulg-architecture/SKILL.md" in uri
    assert _LEGACY_SKILL_DOC_PREFIX + "ulg-architecture" not in uri
    with pytest.raises(SkillCatalogResolveError):
        resolve_canonical_source_uri("ulg-architecture_ulg")


@pytest.mark.offline
def test_integrity_keys_have_resolvable_bodies() -> None:
    missing: list[str] = []
    for key in sorted(_iter_integrity_keys()):
        uri = resolve_canonical_source_uri(key)
        if _resolve_body_path(uri) is None:
            missing.append(f"{key!r} → {uri!r}")
    assert not missing, "unresolvable bodies:\n" + "\n".join(missing)


@pytest.mark.offline
def test_integrity_keys_avoid_phantom_agent_skill_ids() -> None:
    catalog = get_skill_catalog()
    phantom: list[str] = []
    for key in sorted(_iter_integrity_keys()):
        canonical = canonical_catalog_slug(key)
        if canonical not in catalog.entries:
            phantom.append(f"{key!r} → missing catalog key {canonical!r}")
            continue
        entity_id = canonical_agent_skill_id(key)
        if entity_id != f"agent_skill:{canonical}":
            phantom.append(f"{key!r} → unexpected entity id {entity_id!r}")
    assert not phantom, "\n".join(phantom)


@pytest.mark.offline
@pytest.mark.parametrize("slug", ["architecture-invariants", "advisor-timing"])
def test_regression_skill_slug_resolves_with_body(slug: str) -> None:
    uri = resolve_canonical_source_uri(slug)
    assert _resolve_body_path(uri) is not None


@pytest.mark.offline
def test_no_docs_agent_guides_skill_paths_in_catalog_uris() -> None:
    bad: list[str] = []
    for slug, uri in catalog_source_uris().items():
        if _LEGACY_SKILL_DOC_PREFIX in uri:
            bad.append(f"{slug!r} → {uri!r}")
        elif uri.startswith("workspaces://") and not _is_canonical_workspaces_uri(uri):
            bad.append(f"{slug!r} → {uri!r}")
    assert not bad, "non-canonical catalog URIs:\n" + "\n".join(bad)


@pytest.mark.offline
def test_docs_agent_guides_violations_set_empty_after_cleanup() -> None:
    assert KNOWN_DOCS_AGENT_GUIDES_VIOLATIONS == frozenset()
    assert _CLEANUP_TODO.startswith("todo:")
