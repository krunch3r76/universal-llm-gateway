"""Lane-B hub reference-transaction hook (AC1 / AC3)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_sdk_hub_land_scope import (
    ff_only_onto_hub_master,
)
from services.git_integration_worker.cursor_sdk_worktree import mint_dispatch_worktree

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
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict[str, str]] = []

    def _capture(**kwargs: str) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_events."
        "emit_sdk_lane_hub_checkout_refused",
        _capture,
    )
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
    assert code == 1
    assert captured
    assert captured[0]["dispatch_id"] == "hook-emit"


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
