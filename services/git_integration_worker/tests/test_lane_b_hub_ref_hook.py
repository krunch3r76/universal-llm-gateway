"""Lane-B hub reference-transaction hook (AC1 / AC3)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_hub_land_scope import (
    ff_only_onto_hub_master,
)
from services.git_integration_worker.cursor_sdk_worktree import mint_dispatch_worktree
from services.git_integration_worker.lane_b_reference_transaction_hook import (
    HOOK_POLICY_REFUSE_EXIT,
)

pytestmark = pytest.mark.offline

_HOOKS_DIR = Path(__file__).resolve().parents[1] / "hooks"
_ZERO = "0" * 40


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "seed")
    _git(repo, "config", "core.hooksPath", str(_HOOKS_DIR))
    return repo


def _dispatch_env(dispatch_id: str) -> dict[str, str]:
    env = os.environ.copy()
    env["CURSOR_SDK_DISPATCH_ID"] = dispatch_id
    return env


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("CURSOR_SDK_DISPATCH_ID", None)
    return env


@pytest.fixture(autouse=True)
def _clear_dispatch_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CURSOR_SDK_DISPATCH_ID", raising=False)


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    return _init_repo(tmp_path)


def test_hook_script_is_executable_with_shebang() -> None:
    hook = (
        Path(__file__).resolve().parents[1] / "hooks" / "reference-transaction"
    )
    assert hook.is_file()
    first = hook.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("#!")
    assert os.access(hook, os.X_OK)


def test_lane_worktree_commit_with_dispatch_id_allowed(
    source_repo: Path, tmp_path: Path
) -> None:
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "hook-lane-commit"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    (wt / "lane.py").write_text("x=1\n", encoding="utf-8")
    env = _dispatch_env(dispatch_id)
    add = _git(wt, "add", "lane.py", env=env)
    assert add.returncode == 0
    commit = _git(wt, "commit", "-m", "lane work", env=env)
    assert commit.returncode == 0


def test_worker_ff_land_without_dispatch_id_allowed(
    source_repo: Path, tmp_path: Path
) -> None:
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "hook-ff"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    branch = f"cursor-sdk/lane-{dispatch_id}"
    (wt / "ff.py").write_text("ff\n", encoding="utf-8")
    _git(wt, "add", "ff.py")
    _git(wt, "commit", "-m", "ff branch")
    assert ff_only_onto_hub_master(source_repo, branch_name=branch) is True
    assert _git(source_repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "master"


def test_hub_checkout_dash_b_refused_with_dispatch_id(
    source_repo: Path,
) -> None:
    env = _dispatch_env("hook-checkout-b")
    before = _git(source_repo, "rev-parse", "--abbrev-ref", "HEAD", env=env)
    assert before.stdout.strip() == "master"
    checkout = _git(
        source_repo,
        "checkout",
        "-B",
        "cursor-sdk/lane-blocked",
        env=env,
    )
    assert checkout.returncode != 0
    after = _git(source_repo, "rev-parse", "--abbrev-ref", "HEAD", env=env)
    assert after.stdout.strip() == "master"


def test_git_c_hub_checkout_refused_from_lane_cwd(
    source_repo: Path, tmp_path: Path
) -> None:
    worktree_root = tmp_path / "worktrees"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id="hook-c-lane",
    )
    env = _dispatch_env("hook-c-hub")
    checkout = subprocess.run(
        ["git", "-C", str(source_repo), "checkout", "-B", "cursor-sdk/lane-c-block"],
        cwd=str(wt),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert checkout.returncode != 0
    hub_head = _git(source_repo, "rev-parse", "--abbrev-ref", "HEAD", env=env)
    assert hub_head.stdout.strip() == "master"


def test_non_ff_cursor_sdk_ref_update_refused_on_hub(
    source_repo: Path,
) -> None:
    branch = "cursor-sdk/lane-force"
    tip = _git(source_repo, "rev-parse", "HEAD").stdout.strip()
    _git(source_repo, "branch", branch, tip)
    (source_repo / "force.py").write_text("force\n", encoding="utf-8")
    _git(source_repo, "add", "force.py")
    _git(source_repo, "commit", "-m", "hub advance")
    _git(source_repo, "checkout", branch)
    (source_repo / "lane_only.py").write_text("lane\n", encoding="utf-8")
    _git(source_repo, "add", "lane_only.py")
    _git(source_repo, "commit", "-m", "lane tip")
    _git(source_repo, "checkout", "master")
    branch_tip = _git(source_repo, "rev-parse", branch).stdout.strip()
    rewind = tip
    env = _dispatch_env("hook-force")
    before = branch_tip
    update = _git(
        source_repo,
        "update-ref",
        f"refs/heads/{branch}",
        rewind,
        before,
        env=env,
    )
    assert update.returncode != 0
    after = _git(source_repo, "rev-parse", branch, env=env).stdout.strip()
    assert after == before


def test_cursor_sdk_branch_deletion_allowed_from_hub(
    source_repo: Path,
) -> None:
    branch = "cursor-sdk/lane-delete"
    tip = _git(source_repo, "rev-parse", "HEAD").stdout.strip()
    _git(source_repo, "branch", branch, tip)
    env = _dispatch_env("hook-delete")
    delete = _git(source_repo, "update-ref", "-d", f"refs/heads/{branch}", env=env)
    assert delete.returncode == 0
    show = _git(source_repo, "show-ref", "--verify", f"refs/heads/{branch}", env=env)
    assert show.returncode != 0


def test_hub_refuse_emits_signal_in_process(
    source_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict[str, str]] = []

    def _capture(**kwargs: str) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_events."
        "emit_sdk_lane_hub_checkout_refused",
        _capture,
    )
    from services.git_integration_worker.lane_b_reference_transaction_hook import (
        run_reference_transaction_hook,
    )

    tip = _git(source_repo, "rev-parse", "HEAD").stdout.strip()
    stdin = f"0000000000000000000000000000000000000000 {tip} refs/heads/cursor-sdk/lane-emit\n"
    code = run_reference_transaction_hook(
        state="prepared",
        stdin_text=stdin,
        dispatch_id="hook-emit",
        repo=source_repo,
    )
    assert code == HOOK_POLICY_REFUSE_EXIT
    assert captured
    assert captured[0]["dispatch_id"] == "hook-emit"


def test_git_branch_cursor_sdk_refused_on_hub(source_repo: Path) -> None:
    env = _dispatch_env("hook-git-branch")
    branch = "cursor-sdk/lane-branch-cmd"
    create = _git(source_repo, "branch", branch, env=env)
    assert create.returncode != 0
    show = _git(source_repo, "show-ref", "--verify", f"refs/heads/{branch}", env=env)
    assert show.returncode != 0


def test_hub_master_fast_forward_with_dispatch_id(source_repo: Path) -> None:
    env = _dispatch_env("hook-ff-master")
    before = _git(source_repo, "rev-parse", "HEAD", env=env).stdout.strip()
    (source_repo / "ff_master.py").write_text("ff\n", encoding="utf-8")
    _git(source_repo, "add", "ff_master.py", env=env)
    commit = _git(source_repo, "commit", "-m", "ff on master", env=env)
    assert commit.returncode == 0
    after = _git(source_repo, "rev-parse", "HEAD", env=env).stdout.strip()
    assert after != before
    assert _git(source_repo, "rev-parse", "--abbrev-ref", "HEAD", env=env).stdout.strip() == "master"


def test_checkout_existing_branch_allowed_with_dispatch_id(
    source_repo: Path,
) -> None:
    branch = "feature-existing"
    tip = _git(source_repo, "rev-parse", "HEAD").stdout.strip()
    _git(source_repo, "branch", branch, tip)
    env = _dispatch_env("hook-checkout-existing")
    checkout = _git(source_repo, "checkout", branch, env=env)
    assert checkout.returncode == 0
    assert (
        _git(source_repo, "rev-parse", "--abbrev-ref", "HEAD", env=env).stdout.strip()
        == branch
    )


def test_checkout_detach_allowed_with_dispatch_id(source_repo: Path) -> None:
    env = _dispatch_env("hook-detach")
    checkout = _git(source_repo, "checkout", "--detach", env=env)
    assert checkout.returncode == 0
    head = _git(source_repo, "rev-parse", "HEAD", env=env).stdout.strip()
    sym = _git(source_repo, "symbolic-ref", "-q", "HEAD", env=env)
    assert sym.returncode != 0
    assert len(head) == 40


def _init_repo_symlink_hook(tmp_path: Path) -> Path:
    repo = tmp_path / "repo-symlink"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "seed")
    tracked = _HOOKS_DIR / "reference-transaction"
    link = repo / ".git" / "hooks" / "reference-transaction"
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(tracked)
    return repo


def test_symlink_hook_install_lane_commit_and_hub_refuse(
    tmp_path: Path,
) -> None:
    source_repo = _init_repo_symlink_hook(tmp_path)
    worktree_root = tmp_path / "worktrees-symlink"
    dispatch_id = "hook-symlink-lane"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    env = _dispatch_env(dispatch_id)
    (wt / "symlink_lane.py").write_text("x\n", encoding="utf-8")
    assert _git(wt, "add", "symlink_lane.py", env=env).returncode == 0
    assert _git(wt, "commit", "-m", "symlink hook lane", env=env).returncode == 0

    env_hub = _dispatch_env("hook-symlink-refuse")
    assert (
        _git(source_repo, "rev-parse", "--abbrev-ref", "HEAD", env=env_hub).stdout.strip()
        == "master"
    )
    blocked = _git(
        source_repo,
        "checkout",
        "-B",
        "cursor-sdk/lane-symlink-block",
        env=env_hub,
    )
    assert blocked.returncode != 0
    assert (
        _git(source_repo, "rev-parse", "--abbrev-ref", "HEAD", env=env_hub).stdout.strip()
        == "master"
    )


def test_hook_internal_error_fail_open_lane_commit(
    source_repo: Path, tmp_path: Path
) -> None:
    hook_dir = tmp_path / "fail-open-hooks"
    hook_dir.mkdir()
    fake_py = hook_dir / "fake-python"
    fake_py.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_py.chmod(0o755)
    hook = hook_dir / "reference-transaction"
    hook.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
state="${{1:-}}"
if [[ "${{state}}" != "prepared" ]]; then exit 0; fi
if [[ -z "${{CURSOR_SDK_DISPATCH_ID:-}}" ]]; then exit 0; fi
rc=0
"{fake_py}" -m services.git_integration_worker.lane_b_reference_transaction_hook "${{state}}" || rc=$?
if [[ "${{rc}}" -eq 0 ]]; then exit 0; fi
if [[ "${{rc}}" -eq 2 ]]; then exit 1; fi
echo "reference-transaction hook internal error (exit ${{rc}}); allowing transaction" >&2
exit 0
""",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    repo = tmp_path / "repo-fail-open"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "f").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "f")
    _git(repo, "commit", "-m", "seed")
    _git(repo, "config", "core.hooksPath", str(hook_dir))
    worktree_root = tmp_path / "wt-fail-open"
    dispatch_id = "hook-fail-open"
    wt = mint_dispatch_worktree(
        source_repo=repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    env = _dispatch_env(dispatch_id)
    (wt / "fail_open.py").write_text("ok\n", encoding="utf-8")
    assert _git(wt, "add", "fail_open.py", env=env).returncode == 0
    assert _git(wt, "commit", "-m", "fail open lane", env=env).returncode == 0


def test_failed_hub_checkout_emit_does_not_allow_checkout(
    source_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(**_kwargs: str) -> None:
        raise RuntimeError("emit failed")

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_events."
        "emit_sdk_lane_hub_checkout_refused",
        _boom,
    )
    env = _dispatch_env("hook-emit-fail")
    checkout = _git(
        source_repo,
        "checkout",
        "-B",
        "cursor-sdk/lane-emit-fail",
        env=env,
    )
    assert checkout.returncode != 0
    assert _git(source_repo, "rev-parse", "--abbrev-ref", "HEAD", env=env).stdout.strip() == "master"
