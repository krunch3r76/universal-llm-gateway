"""Tests for pytest skill-tree bootstrap (a:40580)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.tests import skill_tree_test_bootstrap as bootstrap


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def hub_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "hub"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "test", cwd=repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)
    skill = repo / ".cursor" / "skills" / "bootstrap-probe"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: bootstrap-probe\n---\n", encoding="utf-8")
    return repo


def test_bootstrap_skips_when_lane_skill_trees_present(
    hub_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When SOT files exist, do not call materialize."""
    lane = tmp_path / "lane"
    lane.mkdir()
    (lane / "README.md").write_text("seed\n", encoding="utf-8")
    probe = lane / ".cursor" / "skills" / "bootstrap-probe" / "SKILL.md"
    probe.parent.mkdir(parents=True)
    probe.write_text("---\nname: bootstrap-probe\n---\n", encoding="utf-8")

    monkeypatch.setattr(bootstrap, "resolve_hub_worktree_root", lambda _lane: hub_repo)
    calls: list[tuple[Path, Path]] = []

    def _record(*, hub_root: Path, lane_root: Path) -> None:
        calls.append((hub_root, lane_root))

    monkeypatch.setattr(bootstrap, "materialize_hub_skill_trees_for_lane", _record)
    monkeypatch.setattr(
        bootstrap,
        "skill_sot_missing",
        lambda _lane: False,
    )
    assert bootstrap.maybe_materialize_skill_trees_for_pytest(lane) is False
    assert calls == []


def test_bootstrap_materializes_from_hub_when_sots_missing(
    hub_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git-clean lane worktree receives hub-only skill dirs before catalog load."""
    lane = tmp_path / "lane-wt"
    _git("worktree", "add", "--detach", str(lane), "master", cwd=hub_repo)
    assert not (
        lane / ".cursor" / "skills" / "bootstrap-probe" / "SKILL.md"
    ).is_file()

    def _probe_paths(lane_root: Path) -> list[Path]:
        return [
            lane_root / ".cursor" / "skills" / "bootstrap-probe" / "SKILL.md"
        ]

    monkeypatch.setattr(bootstrap, "_catalog_sot_paths", _probe_paths)
    assert bootstrap.maybe_materialize_skill_trees_for_pytest(lane) is True
    copied = lane / ".cursor" / "skills" / "bootstrap-probe" / "SKILL.md"
    assert copied.is_file()
    assert "bootstrap-probe" in copied.read_text(encoding="utf-8")

    _git("worktree", "remove", str(lane), "--force", cwd=hub_repo)
