"""Unit tests for CDP cursor_only read-cue helpers."""

from __future__ import annotations

from pathlib import Path

from claude_bundles.cdp_inline_read_cue import (
    READ_BLOCK_HEADING,
    emit_workspaces_fs_read,
    render_cdp_inline_read_block,
    rewrite_inline_use_the_lines,
    workspaces_fs_skill_path,
)


def test_workspaces_fs_skill_path_uses_resolve_sot_relative() -> None:
    repo = Path(__file__).resolve().parents[2]
    sot = repo / "cursor-plugins" / "ulg-ecosystem" / "skills" / "fs" / "SKILL.md"
    assert workspaces_fs_skill_path(sot, repo) == (
        "universal-llm-gateway/cursor-plugins/ulg-ecosystem/skills/fs/SKILL.md"
    )
    assert emit_workspaces_fs_read(sot, repo) == (
        'fs(sandbox="workspaces", op="read", '
        'path="universal-llm-gateway/cursor-plugins/ulg-ecosystem/skills/fs/SKILL.md")'
    )


def test_render_read_block_and_rewrite_use_the() -> None:
    repo = Path("/tmp/repo")
    sot = repo / ".cursor" / "skills" / "architecture-invariants" / "SKILL.md"
    block = render_cdp_inline_read_block(
        [("architecture-invariants", "cursor_only", sot)],
        repo_root=repo,
    )
    assert READ_BLOCK_HEADING in block
    assert "not on this seat's Skill loader" in block
    assert "architecture-invariants" in block
    assert ".cursor/skills/architecture-invariants/SKILL.md" in block
    text = (
        "- Use the `architecture-invariants` skill "
        "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
        "- Use the `reasoning-posture` skill "
        "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    )
    rewritten = rewrite_inline_use_the_lines(text, {"architecture-invariants"})
    assert "Use the `architecture-invariants` skill" not in rewritten
    assert "Read the inlined `architecture-invariants` excerpt" in rewritten
    assert "Use the `reasoning-posture` skill" in rewritten
