"""CAS land updates a checked-out master tree, and refuses divergent dirt."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from git_integrate.git_cas import diff_sha256
from git_integrate.hub_tree_sync import HubSyncPlan, paths_in_commit, plan_hub_sync
from git_integrate.land import land_op


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    (repo / "README.md").write_text("base\n")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "init", cwd=repo)
    return repo


def _arc(source: Path, tmp_path: Path, name: str) -> Path:
    wt = tmp_path / "wt" / name
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-b", f"arc/{name}", str(wt), "master", cwd=source)
    _git("config", "user.email", "test@example.com", cwd=wt)
    _git("config", "user.name", "Test", cwd=wt)
    (wt / "feature.py").write_text(f"# {name}\n")
    _git("add", "feature.py", cwd=wt)
    _git("commit", "-m", f"add {name}", cwd=wt)
    return wt


@pytest.mark.asyncio
async def test_land_op_refuses_divergent_hub_dirt_without_moving_ref(
    tmp_path: Path,
) -> None:
    """The retry loop itself reports NOT landed@working-tree before CAS."""
    source = _repo(tmp_path)
    wt = _arc(source, tmp_path, "divergent")
    _git("checkout", "master", cwd=source)
    (source / "feature.py").write_text("# unrelated wip\n")
    before = (source / "feature.py").read_bytes()
    master_before = _git("rev-parse", "refs/heads/master", cwd=source).stdout.strip()

    out = await land_op(
        arc="divergent",
        phase="sync",
        worktree_path=str(wt),
        approval="approved",
        expected_diff_sha256=diff_sha256(str(wt)),
        source_repo=str(source),
        green_gate_cmd=["true"],
        remove_worktree=False,
    )
    assert out["status"] == "rejected"
    assert out["working_tree"] == "NOT landed@working-tree"
    assert "feature.py" in out["reason"]
    assert "feature.py" in out["hub_porcelain"]
    assert (source / "feature.py").read_bytes() == before
    master_after = _git("rev-parse", "refs/heads/master", cwd=source).stdout.strip()
    assert master_after == master_before


@pytest.mark.asyncio
async def test_land_op_refuses_quoted_path_without_clobber(tmp_path: Path) -> None:
    """A space in the path must still refuse. Text porcelain would miss it."""
    source = _repo(tmp_path)
    wt = tmp_path / "wt" / "spaced"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-b", "arc/spaced", str(wt), "master", cwd=source)
    _git("config", "user.email", "test@example.com", cwd=wt)
    _git("config", "user.name", "Test", cwd=wt)
    spaced = wt / "my file.py"
    spaced.write_text("# lane\n")
    _git("add", "my file.py", cwd=wt)
    _git("commit", "-m", "add spaced", cwd=wt)
    _git("checkout", "master", cwd=source)
    (source / "my file.py").write_text("# operator wip\n")
    before = (source / "my file.py").read_bytes()
    master_before = _git("rev-parse", "refs/heads/master", cwd=source).stdout.strip()

    out = await land_op(
        arc="spaced",
        phase="sync",
        worktree_path=str(wt),
        approval="approved",
        expected_diff_sha256=diff_sha256(str(wt)),
        source_repo=str(source),
        green_gate_cmd=["true"],
        remove_worktree=False,
    )
    assert out["status"] == "rejected"
    assert out["working_tree"] == "NOT landed@working-tree"
    assert (source / "my file.py").read_bytes() == before
    master_after = _git("rev-parse", "refs/heads/master", cwd=source).stdout.strip()
    assert master_after == master_before


@pytest.mark.asyncio
async def test_replan_after_gate_refuses_before_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dirt that appears during the gate is seen again before the ref moves."""
    source = _repo(tmp_path)
    wt = _arc(source, tmp_path, "replan")
    _git("checkout", "master", cwd=source)
    master_before = _git("rev-parse", "refs/heads/master", cwd=source).stdout.strip()
    calls = {"n": 0}
    real = plan_hub_sync

    def _flip(source_repo: str, old_sha: str, new_sha: str) -> HubSyncPlan:
        calls["n"] += 1
        plan = real(source_repo, old_sha, new_sha)
        if calls["n"] >= 2:
            return HubSyncPlan(
                checkout=plan.checkout,
                blocked=True,
                porcelain="?? feature.py",
                checkout_paths=(),
                remove_paths=(),
            )
        return plan

    monkeypatch.setattr("git_integrate.ops_common.plan_hub_sync", _flip)
    out = await land_op(
        arc="replan",
        phase="sync",
        worktree_path=str(wt),
        approval="approved",
        expected_diff_sha256=diff_sha256(str(wt)),
        source_repo=str(source),
        green_gate_cmd=["true"],
        remove_worktree=False,
    )
    assert calls["n"] >= 2
    assert out["status"] == "rejected"
    assert out["working_tree"] == "NOT landed@working-tree"
    master_after = _git("rev-parse", "refs/heads/master", cwd=source).stdout.strip()
    assert master_after == master_before
    assert not (source / "feature.py").exists()


def test_paths_in_commit_uses_first_parent_only(tmp_path: Path) -> None:
    """A merge must not treat the other parent's files as this commit's paths."""
    source = _repo(tmp_path)
    _git("checkout", "-b", "side", cwd=source)
    (source / "side.txt").write_text("side\n")
    _git("add", "side.txt", cwd=source)
    _git("commit", "-m", "side", cwd=source)
    _git("checkout", "master", cwd=source)
    (source / "main.txt").write_text("main\n")
    _git("add", "main.txt", cwd=source)
    _git("commit", "-m", "main", cwd=source)
    _git("merge", "--no-ff", "side", "-m", "merge", cwd=source)
    head = _git("rev-parse", "HEAD", cwd=source).stdout.strip()
    paths = paths_in_commit(str(source), head)
    assert "side.txt" in paths
    assert "main.txt" not in paths
