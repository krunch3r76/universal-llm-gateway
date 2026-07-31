"""Unit tests for agent-surface → skill bundle generation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from gen_rules.agent_skill_bundles import AGENT_SURFACE_SKILL_SLUGS
from gen_rules.agent_guides import AGENT_GUIDES_RULE_SLUGS
from gen_rules.parser import parse_source
from gen_rules.renderer import render_skill_bundle

from claude_bundles.resolver import CURSOR_INDEXED_SLUGS


def _write_source(tmp_path: Path, slug: str, *, with_skill_fm: bool = True) -> Path:
    source = tmp_path / f"{slug}.md"
    fm_block = ""
    if with_skill_fm:
        fm_block = (
            "<!-- frontmatter:skill\n"
            f"name: {slug}\n"
            "description: Test skill description long enough to pass bundle lint checks.\n"
            "-->\n"
        )
    source.write_text(
        fm_block
        + "<!-- target:* -->\n"
        "# Title\n\nBody line one.\n"
        "<!-- /target:* -->\n",
        encoding="utf-8",
    )
    return source


def test_parser_frontmatter_skill(tmp_path: Path) -> None:
    source = _write_source(tmp_path, "sample-slug")
    parsed = parse_source(source)
    assert parsed.frontmatter_skill is not None
    fm = yaml.safe_load(parsed.frontmatter_skill)
    assert fm["name"] == "sample-slug"
    assert "description" in fm


def test_render_skill_bundle_shape(tmp_path: Path) -> None:
    slug = "sample-slug"
    source = _write_source(tmp_path, slug)
    parsed = parse_source(source)
    rendered = render_skill_bundle(
        parsed, source_rel=f"agent-surface/sources/{slug}.md", slug=slug
    )
    assert rendered.startswith("---\n")
    assert f"name: {slug}\n" in rendered
    assert "description:" in rendered.split("---\n", 2)[1]
    assert "DO NOT EDIT" in rendered
    assert "Body line one." in rendered
    assert "# Title" in rendered


def test_render_skill_bundle_requires_frontmatter_skill(tmp_path: Path) -> None:
    source = _write_source(tmp_path, "no-fm", with_skill_fm=False)
    parsed = parse_source(source)
    with pytest.raises(ValueError, match="missing frontmatter:skill"):
        render_skill_bundle(parsed, source_rel="agent-surface/sources/no-fm.md", slug="no-fm")


def test_agent_surface_skill_slugs_subset_assertions() -> None:
    assert set(AGENT_SURFACE_SKILL_SLUGS) <= set(AGENT_GUIDES_RULE_SLUGS)
    assert set(AGENT_SURFACE_SKILL_SLUGS) <= set(CURSOR_INDEXED_SLUGS)
