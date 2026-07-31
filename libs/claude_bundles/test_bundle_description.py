"""Tests for claude.ai bundle description resolution."""

from __future__ import annotations

from claude_bundles.bundle_description import (
    MAX_CLAUDE_AI_DESCRIPTION_LEN,
    MAX_SKILL_DESCRIPTION_LEN,
    MIN_BUNDLE_DESCRIPTION_LEN,
    adapt_skill_md_for_claude_ai,
    extract_rendered_description,
    fit_claude_ai_description,
    is_trigger_grade,
    lint_frontmatter_description,
    parse_frontmatter,
    resolve_bundle_description,
)
from claude_bundles.resolver import render_bundle


def test_max_skill_description_alias() -> None:
    assert MAX_SKILL_DESCRIPTION_LEN == 200
    assert MAX_CLAUDE_AI_DESCRIPTION_LEN == MAX_SKILL_DESCRIPTION_LEN


def test_lint_frontmatter_description_rejects_over_fleet_ceiling() -> None:
    long_desc = "x" * (MAX_SKILL_DESCRIPTION_LEN + 1)
    text = f"---\nname: example\ndescription: {long_desc}\n---\n\n# Example\n"
    msg = lint_frontmatter_description("example", text)
    assert msg is not None
    assert "description_len=" in msg


def test_parse_multiline_frontmatter_description() -> None:
    raw = (
        "---\n"
        "name: thirdparty-api-mirror\n"
        "description: >-\n"
        "  Mirror third-party API docs into the repo for RAG retrieval.\n"
        "  Use when asked to refresh a vendor mirror.\n"
        "---\n\n"
        "# Third-party API mirror\n"
    )
    fm, body = parse_frontmatter(raw)
    assert "Third-party API mirror" in body
    desc = resolve_bundle_description(
        "thirdparty-api-mirror",
        frontmatter=fm,
        body=body,
        entity_description=None,
    )
    assert "Mirror third-party API docs" in desc
    assert is_trigger_grade(desc)


def test_entity_description_fallback_when_frontmatter_title_grade() -> None:
    raw = (
        "---\n"
        "name: fs\n"
        "---\n\n"
        "# Skill: MCP fs Tool\n\n"
        "**Trigger:** On any `fs(...)` call.\n"
    )
    fm, body = parse_frontmatter(raw)
    entity = (
        "On any fs(...) call — read, write, list, search, markdown section ops "
        "and binary ops for cortex, workspaces, and context sandboxes."
    )
    desc = resolve_bundle_description(
        "fs",
        frontmatter=fm,
        body=body,
        entity_description=entity,
    )
    assert desc == entity


def test_render_bundle_uses_entity_fallback() -> None:
    raw = (
        "---\n"
        "name: service-lifecycle\n"
        "---\n\n"
        "# Skill: Service Lifecycle (manage)\n"
    )
    entity = (
        "Start, stop, restart, rebuild, or wait_healthy for gateway services via MCP. "
        "Use when the user asks to manage universal-llm-gateway ecosystem services."
    )
    rendered = render_bundle("service-lifecycle", raw, entity_description=entity)
    assert f"description: {entity}" in rendered or f'description: "{entity}"' in rendered
    assert len(entity) >= MIN_BUNDLE_DESCRIPTION_LEN


def test_fit_claude_ai_description_truncates_at_word_boundary() -> None:
    long = "On entering ANY lead/orchestrator session — " + ("detail " * 40)
    fitted = fit_claude_ai_description(long)
    assert len(fitted) <= MAX_CLAUDE_AI_DESCRIPTION_LEN
    assert fitted.endswith("…")
    assert len(fitted) >= MIN_BUNDLE_DESCRIPTION_LEN


def test_entity_description_preferred_when_frontmatter_over_claude_ai_cap() -> None:
    raw = (
        "---\n"
        "name: demo\n"
        f'description: "{"On any task " + "with many details " * 30}"\n'
        "---\n\n"
        "# Demo\n"
    )
    fm, body = parse_frontmatter(raw)
    entity = (
        "On any demo task — read before acting. Short trigger-grade entity description."
    )
    desc = resolve_bundle_description(
        "demo",
        frontmatter=fm,
        body=body,
        entity_description=entity,
    )
    assert desc == entity
    assert len(desc) <= MAX_CLAUDE_AI_DESCRIPTION_LEN


def test_prepare_ui_upload_zip_contains_skill_md() -> None:
    import tempfile
    import zipfile
    from pathlib import Path

    from claude_bundles.skills_api import prepare_ui_upload_artifact

    raw = (
        "---\n"
        "name: demo\n"
        'description: "On any demo task — read before acting on bounded work items."\n'
        "---\n\n"
        "# Demo body\n"
    )
    with tempfile.TemporaryDirectory() as td:
        bundle = Path(td) / "demo"
        bundle.mkdir()
        (bundle / "SKILL.md").write_text(raw)
        staging = Path(td) / "stage"
        path, n = prepare_ui_upload_artifact(
            bundle / "SKILL.md", staging, slug="demo", fmt="zip"
        )
        assert path.suffix == ".zip"
        assert n <= 200
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            body = zf.read("demo/SKILL.md").decode()
        assert "demo/SKILL.md" in names
        assert "On any demo task" in body


def test_adapt_skill_md_for_claude_ai_preserves_body() -> None:
    raw = (
        "---\n"
        "name: demo\n"
        f'description: "{"x" * 250}"\n'
        "---\n\n"
        "# Body heading\n\n"
        "Keep this paragraph.\n"
    )
    adapted, changed = adapt_skill_md_for_claude_ai(raw)
    assert changed
    assert "# Body heading" in adapted
    assert "Keep this paragraph." in adapted
    assert len(extract_rendered_description(adapted)) <= MAX_CLAUDE_AI_DESCRIPTION_LEN


def test_tag_bearing_frontmatter_description_sanitized_not_body_line() -> None:
    """A frontmatter description carrying an angle-bracket tag must be repaired
    (sanitized tag-free) and win over the longest prose body line — the fix for
    the claude.ai bundle-description garble (arc 4559)."""
    raw = (
        "---\n"
        "name: case-evidence\n"
        'description: "On any evidence retrieval — name the canonical index entity '
        '<case-slug>-document-index before drafting a new assertion."\n'
        "---\n\n"
        "# Case evidence retrieval\n\n"
        "This paragraph is a deliberately long prose body line that would "
        "previously have been emitted as the bundle description because the real "
        "frontmatter description was discarded for containing a tag fragment.\n"
    )
    fm, body = parse_frontmatter(raw)
    desc = resolve_bundle_description(
        "case-evidence", frontmatter=fm, body=body, entity_description=None
    )
    assert desc.startswith("On any evidence retrieval")
    assert "<" not in desc and ">" not in desc
    assert "This paragraph" not in desc
    assert is_trigger_grade(desc)
