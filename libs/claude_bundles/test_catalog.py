"""Tests for ``config/skills.yaml`` catalog authority."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

from claude_bundles.catalog import (
    CatalogValidationError,
    _active_sot_slugs,
    _cursor_parity_catalog_dirs,
    _host_local_cursor_dirnames,
    check_sot_catalog_parity,
    clear_skill_catalog_cache,
    load_skill_catalog,
)
from claude_bundles.resolver import (
    claude_ai_target_slugs,
    surface_class_for_slug,
)

_REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clear_catalog_cache() -> None:
    clear_skill_catalog_cache()
    yield
    clear_skill_catalog_cache()


def test_every_active_cursor_sot_has_exactly_one_row() -> None:
    catalog = load_skill_catalog(repo_root=_REPO)
    host_local_dirs = _host_local_cursor_dirnames(catalog)
    cursor_sots = _active_sot_slugs(_REPO) - host_local_dirs
    catalog_dirs = _cursor_parity_catalog_dirs(catalog)
    assert cursor_sots == catalog_dirs
    assert len(catalog.entries) == len(set(catalog.entries))


def test_check_sot_catalog_parity_passes_on_repo() -> None:
    check_sot_catalog_parity(repo_root=_REPO)


def test_host_local_absences_empty_when_bodies_present() -> None:
    catalog = load_skill_catalog(repo_root=_REPO, validate_sot=True)
    assert catalog.host_local_absences == ()


def test_clean_checkout_host_local_missing_loads_with_report(tmp_path: Path) -> None:
    census_dir = tmp_path / "cursor-plugins" / "ulg-ecosystem" / "skills" / "listed"
    census_dir.mkdir(parents=True)
    (census_dir / "SKILL.md").write_text("# listed\n", encoding="utf-8")
    plugin_root = tmp_path / "cursor-plugins" / "ulg-ecosystem"
    (plugin_root / "SKILLS_CENSUS.txt").write_text("listed\n", encoding="utf-8")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "skills.yaml").write_text(
        "skills:\n"
        "  listed:\n"
        "    surface_class: shared_sync\n"
        "    mcp_surface_required: none\n"
        "  host-cursor:\n"
        "    surface_class: cursor_only\n"
        "    mcp_surface_required: none\n"
        "    host_local: true\n"
        "  host-life:\n"
        "    surface_class: life_local\n"
        "    mcp_surface_required: none\n",
        encoding="utf-8",
    )
    clear_skill_catalog_cache()
    catalog = load_skill_catalog(
        path=config_dir / "skills.yaml",
        repo_root=tmp_path,
        validate_sot=True,
    )
    assert "host-cursor" in catalog.entries
    assert "host-life" in catalog.entries
    slugs = {a.slug for a in catalog.host_local_absences}
    assert slugs == {"host-cursor", "host-life"}


def test_host_local_on_shared_sync_raises(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "skills.yaml").write_text(
        "skills:\n"
        "  bad:\n"
        "    surface_class: shared_sync\n"
        "    mcp_surface_required: none\n"
        "    host_local: true\n",
        encoding="utf-8",
    )
    with pytest.raises(CatalogValidationError, match="host_local is only valid"):
        load_skill_catalog(
            path=config_dir / "skills.yaml",
            repo_root=tmp_path,
            validate_sot=False,
        )


def test_census_without_catalog_row_raises(tmp_path: Path) -> None:
    census_dir = tmp_path / "cursor-plugins" / "ulg-ecosystem"
    census_dir.mkdir(parents=True)
    slug = "orphan-census-slug"
    skill_dir = census_dir / "skills" / slug
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# orphan\n", encoding="utf-8")
    (census_dir / "SKILLS_CENSUS.txt").write_text(f"{slug}\n", encoding="utf-8")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "skills.yaml").write_text(
        "skills:\n  existing-skill:\n"
        "    surface_class: shared_sync\n"
        "    mcp_surface_required: none\n",
        encoding="utf-8",
    )
    clear_skill_catalog_cache()
    with pytest.raises(CatalogValidationError, match="missing catalog rows"):
        load_skill_catalog(
            path=config_dir / "skills.yaml",
            repo_root=tmp_path,
            validate_sot=True,
        )


def test_orphan_catalog_row_raises(tmp_path: Path) -> None:
    census_dir = tmp_path / "cursor-plugins" / "ulg-ecosystem" / "skills" / "listed"
    census_dir.mkdir(parents=True)
    (census_dir / "SKILL.md").write_text("# listed\n", encoding="utf-8")
    plugin_root = tmp_path / "cursor-plugins" / "ulg-ecosystem"
    (plugin_root / "SKILLS_CENSUS.txt").write_text("listed\n", encoding="utf-8")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "skills.yaml").write_text(
        "skills:\n"
        "  listed:\n"
        "    surface_class: shared_sync\n"
        "    mcp_surface_required: none\n"
        "  orphan-catalog-only:\n"
        "    surface_class: cursor_only\n"
        "    mcp_surface_required: none\n",
        encoding="utf-8",
    )
    clear_skill_catalog_cache()
    with pytest.raises(CatalogValidationError, match="catalog cursor rows without Cursor SOT"):
        load_skill_catalog(
            path=config_dir / "skills.yaml",
            repo_root=tmp_path,
            validate_sot=True,
        )


def test_orchestrator_workflow_cursor_only_absent_from_claude_targets() -> None:
    assert surface_class_for_slug("orchestrator-workflow") == "cursor_only"
    assert "orchestrator-workflow" not in claude_ai_target_slugs()
    catalog = load_skill_catalog(repo_root=_REPO)
    assert catalog.mcp_surface_required_for("orchestrator-workflow") == "code"


def test_claude_ai_targets_reject_code_admit_life() -> None:
    catalog = load_skill_catalog(repo_root=_REPO)
    for slug in catalog.claude_ai_targets():
        assert catalog.mcp_surface_required_for(slug) in ("none", "life")
    assert catalog.mcp_surface_required_for("fs") == "life"
    assert "fs" in catalog.claude_ai_targets()

    handle = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        delete=False,
        dir=_REPO / "config",
        encoding="utf-8",
    )
    try:
        handle.write(
            "skills:\n"
            "  bad-code-ui:\n"
            "    surface_class: shared_sync\n"
            "    mcp_surface_required: code\n"
        )
        handle.close()
        with pytest.raises(CatalogValidationError, match="cannot require"):
            load_skill_catalog(
                path=Path(handle.name),
                repo_root=_REPO,
                validate_sot=False,
            )
    finally:
        Path(handle.name).unlink(missing_ok=True)


def test_environment_cannot_alter_placement(monkeypatch: pytest.MonkeyPatch) -> None:
    before = load_skill_catalog(repo_root=_REPO)
    before_map = {
        slug: (e.surface_class, e.mcp_surface_required)
        for slug, e in before.entries.items()
    }
    monkeypatch.setenv("INJECTED_BODY_BUDGET_BYTES", "1")
    monkeypatch.setenv("WORKSPACES_ROOT", "/tmp/not-a-real-workspaces-root")
    monkeypatch.setenv("ENABLE_CONTEXT_TOOLS", "0")
    monkeypatch.setenv("ENABLE_BROWSER_TOOLS", "1")
    clear_skill_catalog_cache()
    after = load_skill_catalog(repo_root=_REPO)
    after_map = {
        slug: (e.surface_class, e.mcp_surface_required)
        for slug, e in after.entries.items()
    }
    assert before_map == after_map
    assert os.environ.get("INJECTED_BODY_BUDGET_BYTES") == "1"


def test_missing_claude_ai_uploads_is_desired_minus_observed() -> None:
    scripts = str(_REPO / "scripts" / "cortex")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from upload_claude_bundles import missing_claude_ai_uploads

    missing = missing_claude_ai_uploads({"fs", "cortex-orientation"})
    assert "fs" not in missing
    assert "orchestrator-workflow" not in missing
    assert "fs" in claude_ai_target_slugs()
    catalog = load_skill_catalog(repo_root=_REPO)
    life_targets = [
        s
        for s in catalog.claude_ai_targets()
        if catalog.mcp_surface_required_for(s) == "life"
    ]
    assert life_targets, "expected at least one life Claude.ai target"
