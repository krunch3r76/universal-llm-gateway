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


def _stub_check_scans(monkeypatch: pytest.MonkeyPatch) -> None:
    import ingest_skills

    monkeypatch.setattr(ingest_skills, "_scan_skills", lambda _root: {"probe": {}})
    monkeypatch.setattr(ingest_skills, "_scan_life_local_skills", lambda _root: {})
    monkeypatch.setattr(ingest_skills, "_scan_cortex_sot_metadata", lambda _root: {})
    monkeypatch.setattr(ingest_skills, "_scan_cortex_sot_declared", lambda _root: {})
    monkeypatch.setattr(ingest_skills, "_scan_cortex_sot_skills", lambda _root: {})
    monkeypatch.setattr(ingest_skills, "make_sync_client", lambda _url: object())
    monkeypatch.setattr(
        ingest_skills, "_description_length_failures", lambda _merged: []
    )


@pytest.mark.offline
def test_check_unexcused_drift_does_not_print_ok(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unexcused drift must name the slug and exit 1; OK is a clean run only.

    The previous check dropped holdout lines and then printed OK while
    ``drift_count`` still counted them. A slug outside the holdout set is
    the failing case this guards.
    """
    import ingest_skills

    _stub_check_scans(monkeypatch)
    line = "agent_skill:not-a-holdout missing from cortex"
    monkeypatch.setattr(ingest_skills, "_drifts", lambda *_a, **_k: [line])
    code = ingest_skills.main(["--check", "--report"])
    out, err = capsys.readouterr()
    assert code == 1
    assert "OK ingest_skills --check" not in out
    assert "not-a-holdout" in err
    assert '"drift_count": 1' in out


@pytest.mark.offline
def test_check_holdout_is_printed_excuse_not_silent_ok(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Existing holdouts stay non-failing only when named with a reason."""
    import ingest_skills

    _stub_check_scans(monkeypatch)
    line = "agent_skill:refine-pipeline missing from cortex"
    monkeypatch.setattr(ingest_skills, "_drifts", lambda *_a, **_k: [line])
    code = ingest_skills.main(["--check", "--report"])
    out, err = capsys.readouterr()
    assert code == 0
    assert "EXCUSED: refine-pipeline —" in err
    assert '"drift_count": 0' in out
    assert '"clean": true' in out
