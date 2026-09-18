"""Unit tests for CDP cursor_only read-cue helpers."""

from __future__ import annotations

from pathlib import Path

from claude_bundles.cdp_inline_read_cue import (
    READ_BLOCK_HEADING,
    _SKILL_POINTER_LINE_RE,
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


def test_skill_pointer_line_re_does_not_match_doorbell_unbackticked_use_line() -> None:
    """AC3 — widening the regex would false-positive on successor-wake prose."""
    doorbell_line = "Use the liaison skill. LOAD the liaison skill body; do not skim."
    assert _SKILL_POINTER_LINE_RE.search(doorbell_line) is None


def test_skill_pointer_rewrite_preserves_backticked_use_inside_inlined_excerpts() -> None:
    """AC3 — backticked Use-lines inside verbatim liaison/ulg excerpts must survive."""
    liaison_excerpt = (
        "harvests → folds → decides → dispatches → checkpoints → hops\n"
        "**Use the `git-posture` skill § Land** (merge the lane; keep both hunks).\n"
    )
    ulg_excerpt = (
        "ULG Architecture\n"
        "Pair with Use the `architecture-invariants` skill for transport.\n"
    )
    text = f"{liaison_excerpt}\n{ulg_excerpt}"
    rewritten = rewrite_inline_use_the_lines(text, {"liaison", "ulg-architecture"})
    assert "Use the `git-posture` skill" in rewritten
    assert "Use the `architecture-invariants` skill" in rewritten
