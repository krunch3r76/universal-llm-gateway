"""Tests for catalog-backed skill resolver."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from claude_bundles.catalog import get_skill_catalog
from implement_admission.skill_catalog_resolver import (
    SkillCatalogResolveError,
    catalog_digest,
    catalog_source_uris,
    canonical_catalog_slug,
    resolve_canonical_source_uri,
)
from implement_admission.skill_catalog_freshness import check_catalog_valid

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATOR = _REPO_ROOT / "scripts" / "cortex" / "validate_skill_catalog.py"
_MODULE = _REPO_ROOT / "libs" / "implement_admission" / "skill_catalog_resolver.py"


@pytest.mark.offline
def test_catalog_digest_is_stable_sha256() -> None:
    digest = catalog_digest()
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


@pytest.mark.offline
def test_catalog_load_passes_freshness_gate() -> None:
    assert check_catalog_valid() is None


@pytest.mark.offline
def test_reader_known_and_alias() -> None:
    assert resolve_canonical_source_uri("architecture-invariants").startswith(
        "workspaces://"
    )
    assert resolve_canonical_source_uri("rule:architecture-invariants").startswith(
        "workspaces://"
    )
    assert (
        resolve_canonical_source_uri("session-close-kernel")
        == resolve_canonical_source_uri("session-close")
    )
    assert resolve_canonical_source_uri("ulg-architecture").startswith("workspaces://")


@pytest.mark.offline
def test_reader_missing_key_raises() -> None:
    with pytest.raises(SkillCatalogResolveError, match="absent from skill catalog"):
        resolve_canonical_source_uri("custom-skill-absent-from-source-table")


@pytest.mark.offline
def test_reader_resolves_plugin_and_checkout_slugs() -> None:
    assert resolve_canonical_source_uri("descriptor-authoring-discipline")
    assert resolve_canonical_source_uri("path-sim")
    catalog = get_skill_catalog()
    if "frontier-model-instructions" in catalog.entries:
        assert resolve_canonical_source_uri("frontier-model-instructions")


@pytest.mark.offline
def test_catalog_source_uris_cover_all_slugs() -> None:
    catalog = get_skill_catalog()
    uris = catalog_source_uris()
    assert set(uris) == set(catalog.entries)
    for uri in uris.values():
        assert uri.startswith("workspaces://universal-llm-gateway/")


@pytest.mark.offline
def test_validate_script_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(_GENERATOR), "--check"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout or result.stderr
    assert "OK skill catalog" in result.stdout


@pytest.mark.offline
def test_formatter_stable() -> None:
    result = subprocess.run(
        ["ruff", "format", "--check", str(_MODULE)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout or result.stderr


@pytest.mark.offline
def test_no_live_entity_get_in_hot_path() -> None:
    source = _MODULE.read_text(encoding="utf-8")
    assert "make_sync_client" not in source
    assert source.replace('``entity_get``', "").count("entity_get") == 0


@pytest.mark.offline
def test_session_close_alias_canonicalizes() -> None:
    assert canonical_catalog_slug("session-close") == "session-close-kernel"
