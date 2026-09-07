"""Tests for Cursor filesystem SoT resolution (three-layer precedence)."""

from __future__ import annotations

from pathlib import Path

import pytest

from skills_mount.cursor_fs import (
    CursorSkillSotError,
    classify_cursor_skills,
    resolve_cursor_skill_sot,
)

# Real catalog rows, chosen for their surface_class so the layer assertions below
# test routing rather than a fixture's opinion of it.
_PLUGIN_SLUG = "reasoning-posture"  # shared_sync, plugin census
_CURSOR_SLUG = "ulg-architecture"  # cursor_only, .cursor/skills
_LIFE_SLUG = "prose-discipline"  # life_local, .claude/skills


def _write(root: Path, relpath: str, slug: str, body: str = "# body\n") -> Path:
    path = root / relpath / slug / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.offline
def test_plugin_census_wins_over_workspace_layer(tmp_path: Path) -> None:
    plugin = _write(
        tmp_path, "cursor-plugins/ulg-ecosystem/skills", _PLUGIN_SLUG, "# plugin\n"
    )
    _write(tmp_path, ".cursor/skills", _PLUGIN_SLUG, "# workspace\n")

    sot = resolve_cursor_skill_sot(_PLUGIN_SLUG, repo_root=tmp_path)

    assert sot.layer == "plugin"
    assert sot.path == plugin


@pytest.mark.offline
def test_workspace_layer_resolves_when_plugin_absent(tmp_path: Path) -> None:
    expected = _write(tmp_path, ".cursor/skills", _CURSOR_SLUG)

    sot = resolve_cursor_skill_sot(_CURSOR_SLUG, repo_root=tmp_path)

    assert sot.layer == "workspace"
    assert sot.path == expected


@pytest.mark.offline
def test_life_local_body_resolves_from_claude_skills(tmp_path: Path) -> None:
    """The gap this module closes: life_local slugs have no Cursor-layer body.

    ``config/skills.yaml`` routes them to claude.ai and catalog validation forbids
    them a ``.cursor/skills`` SoT, so ``.claude/skills`` is the only body that
    exists — and a seat told to use one had nothing to resolve before this.
    """
    expected = _write(tmp_path, ".claude/skills", _LIFE_SLUG)

    sot = resolve_cursor_skill_sot(_LIFE_SLUG, repo_root=tmp_path)

    assert sot.layer == "life_local"
    assert sot.path == expected


@pytest.mark.offline
def test_worktree_without_claude_tree_cannot_resolve_life_local(tmp_path: Path) -> None:
    """``.claude`` is gitignored, so a Lane-B worktree legitimately lacks it.

    Callers must anchor resolution on the hub source repo; this asserts the
    failure is loud rather than a silent empty mount.
    """
    with pytest.raises(CursorSkillSotError, match="no Cursor-discoverable SKILL.md"):
        resolve_cursor_skill_sot(_LIFE_SLUG, repo_root=tmp_path)


@pytest.mark.offline
def test_off_catalog_slug_rejected(tmp_path: Path) -> None:
    _write(tmp_path, ".cursor/skills", "definitely-not-a-skill")

    with pytest.raises(CursorSkillSotError, match="absent from skill catalog"):
        resolve_cursor_skill_sot("definitely-not-a-skill", repo_root=tmp_path)


@pytest.mark.offline
def test_entity_id_prefix_canonicalizes(tmp_path: Path) -> None:
    _write(tmp_path, ".cursor/skills", _CURSOR_SLUG)

    sot = resolve_cursor_skill_sot(f"agent_skill:{_CURSOR_SLUG}", repo_root=tmp_path)

    assert sot.canonical_slug == _CURSOR_SLUG
    assert sot.requested_id == f"agent_skill:{_CURSOR_SLUG}"


@pytest.mark.offline
def test_empty_slug_rejected(tmp_path: Path) -> None:
    with pytest.raises(CursorSkillSotError, match="empty skill id"):
        resolve_cursor_skill_sot("   ", repo_root=tmp_path)


@pytest.mark.offline
def test_classify_collects_failures_instead_of_raising(tmp_path: Path) -> None:
    _write(tmp_path, ".cursor/skills", _CURSOR_SLUG)

    resolution = classify_cursor_skills(
        [_CURSOR_SLUG, "definitely-not-a-skill", _LIFE_SLUG],
        repo_root=tmp_path,
    )

    assert [row.canonical_slug for row in resolution.resolved] == [_CURSOR_SLUG]
    assert resolution.unresolved_slugs == ("definitely-not-a-skill", _LIFE_SLUG)


@pytest.mark.offline
def test_classify_dedupes_canonical_duplicates(tmp_path: Path) -> None:
    _write(tmp_path, ".cursor/skills", _CURSOR_SLUG)

    resolution = classify_cursor_skills(
        [_CURSOR_SLUG, f"agent_skill:{_CURSOR_SLUG}", "", "  "],
        repo_root=tmp_path,
    )

    assert len(resolution.resolved) == 1
    assert resolution.resolved[0].requested_id == _CURSOR_SLUG
    assert resolution.unresolved == ()


@pytest.mark.offline
def test_classify_empty_input_is_noop(tmp_path: Path) -> None:
    for empty in (None, [], ()):
        resolution = classify_cursor_skills(empty, repo_root=tmp_path)
        assert resolution.resolved == ()
        assert resolution.unresolved == ()
