"""Tests for canonical skill bundle resolution."""

from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path

import pytest
from claude_bundles.bundle_description import parse_frontmatter

from skills_mount.resolve import (
    MAX_INLINE_SKILL_BASE64_BYTES,
    SkillMountResolveError,
    resolve_skill_bundles,
)


@pytest.fixture
def roots(tmp_path: Path) -> Path:
    workspaces = tmp_path / "repo"
    workspaces.mkdir()
    ws_skill_dir = workspaces / ".cursor/skills/ulg-architecture"
    ws_skill_dir.mkdir(parents=True)
    ulg_md = (
        "# ULG architecture\n\n"
        "Long-form architecture guidance for the universal LLM gateway stack."
    )
    (ws_skill_dir / "SKILL.md").write_text(ulg_md, encoding="utf-8")
    no_fm = "# Consult routing\n\nRoute consult requests to the correct transport."
    consult_dir = workspaces / ".cursor/skills/consult-routing"
    consult_dir.mkdir(parents=True)
    (consult_dir / "SKILL.md").write_text(no_fm, encoding="utf-8")
    return workspaces


def test_alias_resolves_via_canonical_catalog_slug(roots: Path) -> None:
    bundles = resolve_skill_bundles(
        ["ulg-architecture"],
        workspaces_root=roots,
    )
    assert len(bundles) == 1
    assert bundles[0].canonical_slug == "ulg-architecture"


def test_frontmatterless_source_renders_name_and_description(roots: Path) -> None:
    bundles = resolve_skill_bundles(
        ["consult-routing"],
        workspaces_root=roots,
    )
    rendered = _decode_single_skill_md(bundles[0])
    fm, _ = parse_frontmatter(rendered)
    assert str(fm.get("name") or "").strip()
    assert str(fm.get("description") or "").strip()
    assert bundles[0].description.strip()


def test_zip_contains_single_canonical_skill_md(roots: Path) -> None:
    bundles = resolve_skill_bundles(
        ["ulg-architecture"],
        workspaces_root=roots,
    )
    names = _zip_member_names(bundles[0].data_base64)
    assert names == ["ulg-architecture/SKILL.md"]


def test_unknown_id_raises(roots: Path) -> None:
    with pytest.raises(SkillMountResolveError, match="absent from skill catalog"):
        resolve_skill_bundles(
            ["definitely-not-a-skill"],
            workspaces_root=roots,
        )


def test_unmappable_uri_raises(roots: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _bad_uri(slug: str) -> str:
        if slug == "architecture-invariants":
            return "workspaces://universal-llm-gateway/missing/architecture-invariants.md"
        from implement_admission.skill_catalog_resolver import resolve_canonical_source_uri as real

        return real(slug)

    monkeypatch.setattr(
        "implement_admission.skill_catalog_resolver.resolve_canonical_source_uri",
        _bad_uri,
    )
    with pytest.raises(SkillMountResolveError, match="unmappable or unreadable"):
        resolve_skill_bundles(
            ["architecture-invariants"],
            workspaces_root=roots,
        )


def test_oversize_bundle_rejected(roots: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "skills_mount.resolve.MAX_INLINE_SKILL_BASE64_BYTES",
        32,
    )
    with pytest.raises(SkillMountResolveError, match="exceeds max base64 length"):
        resolve_skill_bundles(
            ["ulg-architecture"],
            workspaces_root=roots,
        )


def test_base64_length_guard_constant() -> None:
    assert MAX_INLINE_SKILL_BASE64_BYTES == 70_254_592


def _zip_member_names(data_base64: str) -> list[str]:
    raw = base64.b64decode(data_base64)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        return sorted(zf.namelist())


def _decode_single_skill_md(bundle) -> str:
    raw = base64.b64decode(bundle.data_base64)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        name = f"{bundle.canonical_slug}/SKILL.md"
        return zf.read(name).decode("utf-8")
