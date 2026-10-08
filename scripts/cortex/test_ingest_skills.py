"""Offline unit tests for ingest skill source_uri resolution (catalog/Cursor SOT)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_CORTEX = Path(__file__).resolve().parent
_REPO = _SCRIPTS_CORTEX.parent.parent
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from _skill_constants import _SUPPRESSED, _WS  # noqa: E402
from _skill_scan import _source_uri  # noqa: E402
from implement_admission.skill_catalog_resolver import (  # noqa: E402
    SkillCatalogResolveError,
    resolve_canonical_source_uri,
)

_CURSOR_SOT_SLUGS = (
    "build-pipeline",
    "consult-routing",
    "dispatch-shape",
    "git-posture",
    "agent-guidance-writing",
    "architecture-invariants",
    "ulg-architecture",
    "friction-review",
    "refine-pipeline",
    "handoff-packet-authoring",
    "research-article-ingest",
    "debug-with-events",
    "add-mcp-tool",
    "multi-model-review",
)


@pytest.mark.offline
@pytest.mark.parametrize("slug", _CURSOR_SOT_SLUGS)
def test_source_uri_resolves_to_cursor_sot(slug: str) -> None:
    """Catalog URI, including plugin-only SoT with no hub ``.cursor/skills`` stub."""
    expected = resolve_canonical_source_uri(slug)
    assert _source_uri(slug, "", _REPO) == expected
    rel = expected.removeprefix(f"{_WS}/")
    assert rel != expected
    resolved = _REPO / rel
    assert resolved.is_file(), resolved


@pytest.mark.offline
def test_source_uri_ignores_cross_ref_docs_paths(tmp_path: Path) -> None:
    slug = "consult-routing"
    body = (
        "See universal-llm-gateway/docs/agent-guides/skills/friction-review.md "
        "for related guidance."
    )
    resolved = _source_uri(slug, body, tmp_path)
    assert resolved == resolve_canonical_source_uri(slug)
    assert "docs/" not in resolved


@pytest.mark.offline
def test_source_uri_plugin_slug_uses_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = f"{_WS}/cursor-plugins/ulg-ecosystem/skills/abstraction-layering/SKILL.md"

    def _resolve(slug: str) -> str:
        if slug == "abstraction-layering":
            return plugin
        raise SkillCatalogResolveError(slug)

    monkeypatch.setattr(
        "implement_admission.skill_catalog_resolver.resolve_canonical_source_uri",
        _resolve,
    )
    body = "SOT: .cursor/skills/abstraction-layering/SKILL.md"
    assert _source_uri("abstraction-layering", body, _REPO) == plugin


@pytest.mark.offline
def test_source_uri_cursor_skills_slug_uses_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = f"{_WS}/.cursor/skills/build-pipeline/SKILL.md"

    def _resolve(slug: str) -> str:
        if slug == "build-pipeline":
            return cursor
        raise SkillCatalogResolveError(slug)

    monkeypatch.setattr(
        "implement_admission.skill_catalog_resolver.resolve_canonical_source_uri",
        _resolve,
    )
    assert _source_uri("build-pipeline", "no sot line", _REPO) == cursor


@pytest.mark.offline
def test_source_uri_non_catalog_falls_back_to_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _resolve(slug: str) -> str:
        raise SkillCatalogResolveError(slug)

    monkeypatch.setattr(
        "implement_admission.skill_catalog_resolver.resolve_canonical_source_uri",
        _resolve,
    )
    slug = "not-a-catalog-skill"
    assert _source_uri(slug, "plain body", _REPO) == (
        f"{_WS}/.cursor/skills/{slug}/SKILL.md"
    )


@pytest.mark.offline
def test_delegate_to_grok_suppressed_lifecycle_unchanged() -> None:
    assert "deprecated" in _SUPPRESSED
