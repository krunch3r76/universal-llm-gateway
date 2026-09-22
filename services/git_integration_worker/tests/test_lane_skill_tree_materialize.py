"""Mint-time hub skill-tree materialization (lane-mint skill parity)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker import cursor_sdk_worktree as wt_mod
from services.git_integration_worker.cursor_sdk_worktree import mint_dispatch_worktree
from services.git_integration_worker.lane_skill_tree_materialize import (
    materialize_hub_skill_trees_for_lane,
)


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "hub"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "test", cwd=repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)
    return repo


def test_materialize_copies_gitignored_claude_tree(
    source_repo: Path, tmp_path: Path
) -> None:
    """Hub-only ``.claude/skills`` bodies must land in the minted lane tree."""
    life = source_repo / ".claude" / "skills" / "life-example"
    life.mkdir(parents=True)
    (life / "SKILL.md").write_text(
        "---\nname: life-example\n---\nbody\n", encoding="utf-8"
    )
    lane = tmp_path / "lane-wt"
    lane.mkdir()
    (lane / "README.md").write_text("seed\n", encoding="utf-8")
    materialize_hub_skill_trees_for_lane(hub_root=source_repo, lane_root=lane)
    copied = lane / ".claude" / "skills" / "life-example" / "SKILL.md"
    assert copied.is_file()
    assert "life-example" in copied.read_text(encoding="utf-8")


def test_mint_without_materialize_omits_hub_only_skills(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falsifier: disabling materialize reproduces the missing-SOT mint gap."""
    hub_skill = source_repo / ".cursor" / "skills" / "hub-only-skill"
    hub_skill.mkdir(parents=True)
    (hub_skill / "SKILL.md").write_text(
        "---\nname: hub-only-skill\n---\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        wt_mod,
        "materialize_hub_skill_trees_for_lane",
        lambda **_: None,
    )
    worktree_root = tmp_path / "worktrees"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id="mat-off",
    )
    assert not (wt / ".cursor" / "skills" / "hub-only-skill" / "SKILL.md").is_file()


def test_mint_materializes_hub_only_skills(
    source_repo: Path, tmp_path: Path
) -> None:
    worktree_root = tmp_path / "worktrees"
    hub_skill = source_repo / ".cursor" / "skills" / "hub-only-skill"
    hub_skill.mkdir(parents=True)
    (hub_skill / "SKILL.md").write_text(
        "---\nname: hub-only-skill\n---\n", encoding="utf-8"
    )
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id="mat-on",
    )
    assert (wt / ".cursor" / "skills" / "hub-only-skill" / "SKILL.md").is_file()
