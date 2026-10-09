"""Forward-only git hooks (reference-transaction + pre-push), dispatch and attended."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_worktree import mint_dispatch_worktree

pytestmark = pytest.mark.offline

_HOOKS_DIR = Path(__file__).resolve().parents[1] / "hooks"


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _env_with_dispatch(dispatch_id: str | None) -> dict[str, str]:
    env = os.environ.copy()
    if dispatch_id is None:
        env.pop("CURSOR_SDK_DISPATCH_ID", None)
    else:
        env["CURSOR_SDK_DISPATCH_ID"] = dispatch_id
    return env


def _init_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "seed")
    _git(repo, "config", "core.hooksPath", str(_HOOKS_DIR))
    return repo


def _commit_file(repo: Path, rel: str, text: str, msg: str) -> str:
    (repo / rel).write_text(text, encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "commit", "-m", msg)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _add_bare_origin(repo: Path, tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    bare.mkdir()
    _git(bare, "init", "--bare", "-b", "master")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-u", "origin", "master")
    return bare


@pytest.fixture(params=[None, "forward-only-dispatch"])
def dispatch_ctx(request: pytest.FixtureRequest) -> str | None:
    return request.param


@pytest.fixture(autouse=True)
def _clear_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CURSOR_SDK_DISPATCH_ID", raising=False)


def test_reset_hard_master_refused(source_repo: Path, dispatch_ctx: str | None) -> None:
    repo = source_repo
    env = _env_with_dispatch(dispatch_ctx)
    _commit_file(repo, "a.txt", "a\n", "second")
    at_tip = _git(repo, "rev-parse", "HEAD", env=env).stdout.strip()
    reset = _git(repo, "reset", "--hard", "HEAD~1", env=env)
    assert reset.returncode != 0
    assert _git(repo, "rev-parse", "HEAD", env=env).stdout.strip() == at_tip


def test_branch_force_master_refused(source_repo: Path, dispatch_ctx: str | None) -> None:
    env = _env_with_dispatch(dispatch_ctx)
    tip = _git(source_repo, "rev-parse", "HEAD", env=env).stdout.strip()
    _commit_file(source_repo, "b.txt", "b\n", "advance")
    force = _git(source_repo, "branch", "-f", "master", tip, env=env)
    assert force.returncode != 0
    assert _git(source_repo, "rev-parse", "HEAD", env=env).stdout.strip() != tip


def test_switch_cap_master_refused(source_repo: Path, dispatch_ctx: str | None) -> None:
    env = _env_with_dispatch(dispatch_ctx)
    old = _git(source_repo, "rev-parse", "HEAD", env=env).stdout.strip()
    _commit_file(source_repo, "c.txt", "c\n", "tip")
    switch = _git(source_repo, "switch", "-C", "master", old, env=env)
    assert switch.returncode != 0


def test_checkout_dash_b_existing_ref_refused(
    source_repo: Path, dispatch_ctx: str | None
) -> None:
    env = _env_with_dispatch(dispatch_ctx)
    base = _git(source_repo, "rev-parse", "HEAD", env=env).stdout.strip()
    _commit_file(source_repo, "d.txt", "d\n", "second")
    tip = _git(source_repo, "rev-parse", "HEAD", env=env).stdout.strip()
    _git(source_repo, "branch", "feature-x", tip, env=env)
    _git(source_repo, "checkout", "feature-x", env=env)
    _commit_file(source_repo, "d2.txt", "d2\n", "feature only")
    _git(source_repo, "checkout", "master", env=env)
    checkout = _git(source_repo, "checkout", "-B", "feature-x", base, env=env)
    assert checkout.returncode != 0


def test_checkout_new_branch_allowed(source_repo: Path, dispatch_ctx: str | None) -> None:
    env = _env_with_dispatch(dispatch_ctx)
    create = _git(source_repo, "checkout", "-b", "feature-new", env=env)
    assert create.returncode == 0


def test_git_branch_new_ref_allowed(source_repo: Path, dispatch_ctx: str | None) -> None:
    env = _env_with_dispatch(dispatch_ctx)
    create = _git(source_repo, "branch", "cursor-sdk/lane-new", env=env)
    assert create.returncode == 0


def test_worktree_add_new_branch_allowed(
    source_repo: Path, tmp_path: Path, dispatch_ctx: str | None
) -> None:
    env = _env_with_dispatch(dispatch_ctx)
    wt = tmp_path / "wt-new"
    add = _git(
        source_repo,
        "worktree",
        "add",
        "-b",
        "wt-feature",
        str(wt),
        "master",
        env=env,
    )
    assert add.returncode == 0


def test_ff_merge_master_allowed(source_repo: Path, dispatch_ctx: str | None) -> None:
    env = _env_with_dispatch(dispatch_ctx)
    tip = _git(source_repo, "rev-parse", "HEAD", env=env).stdout.strip()
    _git(source_repo, "checkout", "-b", "merge-me", tip)
    _commit_file(source_repo, "m.txt", "m\n", "branch commit")
    _git(source_repo, "checkout", "master")
    merge = _git(source_repo, "merge", "--ff-only", "merge-me", env=env)
    assert merge.returncode == 0


def test_true_merge_master_allowed(source_repo: Path, dispatch_ctx: str | None) -> None:
    env = _env_with_dispatch(dispatch_ctx)
    tip = _git(source_repo, "rev-parse", "HEAD", env=env).stdout.strip()
    _git(source_repo, "checkout", "-b", "side", tip)
    _commit_file(source_repo, "side.txt", "s\n", "side")
    _git(source_repo, "checkout", "master")
    _commit_file(source_repo, "master.txt", "m\n", "master")
    merge = _git(source_repo, "merge", "side", env=env)
    assert merge.returncode == 0


def test_cherry_pick_forward_allowed(source_repo: Path, dispatch_ctx: str | None) -> None:
    env = _env_with_dispatch(dispatch_ctx)
    tip = _git(source_repo, "rev-parse", "HEAD", env=env).stdout.strip()
    _git(source_repo, "checkout", "-b", "pick-src", tip)
    _commit_file(source_repo, "pick.txt", "p\n", "pick me")
    pick_sha = _git(source_repo, "rev-parse", "HEAD", env=env).stdout.strip()
    _git(source_repo, "checkout", "master")
    pick = _git(source_repo, "cherry-pick", pick_sha, env=env)
    assert pick.returncode == 0


def test_rebase_master_backward_refused(source_repo: Path, dispatch_ctx: str | None) -> None:
    env = _env_with_dispatch(dispatch_ctx)
    base = _git(source_repo, "rev-parse", "HEAD", env=env).stdout.strip()
    _commit_file(source_repo, "r1.txt", "1\n", "one")
    _commit_file(source_repo, "r2.txt", "2\n", "two")
    rebase = _git(source_repo, "rebase", "--onto", base, "HEAD~1", env=env)
    assert rebase.returncode != 0


def test_linked_worktree_origin_master_dash_b_refused(
    source_repo: Path, tmp_path: Path, dispatch_ctx: str | None
) -> None:
    env = _env_with_dispatch(dispatch_ctx)
    bare = _add_bare_origin(source_repo, tmp_path)
    _commit_file(source_repo, "local.txt", "local\n", "ahead of origin")
    worktree_root = tmp_path / "wts"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id="fwd-linked",
    )
    origin_tip = _git(source_repo, "rev-parse", "origin/master", env=env).stdout.strip()
    checkout = subprocess.run(
        ["git", "-C", str(source_repo), "checkout", "-B", "master", "origin/master"],
        cwd=str(wt),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert checkout.returncode != 0
    assert _git(source_repo, "rev-parse", "HEAD", env=env).stdout.strip() != origin_tip
    _ = bare


def test_non_master_branch_delete_allowed(
    source_repo: Path, dispatch_ctx: str | None
) -> None:
    env = _env_with_dispatch(dispatch_ctx)
    tip = _git(source_repo, "rev-parse", "HEAD", env=env).stdout.strip()
    _git(source_repo, "branch", "drop-me", tip)
    delete = _git(source_repo, "branch", "-d", "drop-me", env=env)
    assert delete.returncode == 0


def test_ff_push_allowed(source_repo: Path, tmp_path: Path, dispatch_ctx: str | None) -> None:
    env = _env_with_dispatch(dispatch_ctx)
    _add_bare_origin(source_repo, tmp_path)
    _commit_file(source_repo, "push.txt", "p\n", "push me")
    push = _git(source_repo, "push", "origin", "master", env=env)
    assert push.returncode == 0


def test_force_push_refused(source_repo: Path, tmp_path: Path, dispatch_ctx: str | None) -> None:
    env = _env_with_dispatch(dispatch_ctx)
    _add_bare_origin(source_repo, tmp_path)
    _commit_file(source_repo, "push2.txt", "p2\n", "ahead")
    _git(source_repo, "push", "origin", "master", env=env)
    force = _git(source_repo, "push", "--force", "origin", "HEAD~1:master", env=env)
    assert force.returncode != 0


def test_delete_remote_master_refused(
    source_repo: Path, tmp_path: Path, dispatch_ctx: str | None
) -> None:
    env = _env_with_dispatch(dispatch_ctx)
    _add_bare_origin(source_repo, tmp_path)
    delete = _git(source_repo, "push", "origin", "--delete", "master", env=env)
    assert delete.returncode != 0


def test_delete_remote_non_master_allowed(
    source_repo: Path, tmp_path: Path, dispatch_ctx: str | None
) -> None:
    env = _env_with_dispatch(dispatch_ctx)
    _add_bare_origin(source_repo, tmp_path)
    tip = _git(source_repo, "rev-parse", "HEAD", env=env).stdout.strip()
    _git(source_repo, "branch", "release", tip)
    _git(source_repo, "push", "origin", "release", env=env)
    delete = _git(source_repo, "push", "origin", "--delete", "release", env=env)
    assert delete.returncode == 0


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    return _init_repo(tmp_path)
