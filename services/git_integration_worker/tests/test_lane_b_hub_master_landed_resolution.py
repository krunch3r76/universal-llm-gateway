"""Lane-B §2 landed resolver — hub-master commit when lane meter reads zero (a:35354)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_home import dispatch_git_identity
from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding
from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet
from services.git_integration_worker.cursor_sdk_closeout.closeout_records import (
    SdkRunOutcome,
)
from services.git_integration_worker.cursor_sdk_closeout.delivery_assembly.lane_settlement import (
    settle_lane_and_dispatch_fields,
)
from services.git_integration_worker.cursor_sdk_deliverables_expected import (
    HUB_MASTER_HEAD_RECOVERED,
    HUB_MASTER_HEAD_RECOVERY_NOT_FOUND,
)
from services.git_integration_worker.cursor_sdk_lane_b_commit import (
    branch_state,
    commit_on_terminal,
)
from services.git_integration_worker.cursor_sdk_worktree import (
    mint_dispatch_worktree,
    resolve_master_branch_point,
)


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "test", cwd=repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)
    return repo


def _cfg(source_repo: Path, worktree_root: Path) -> WorkerConfig:
    return WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace=source_repo.parent / "dispatch_ws",
        green_gate_cmd=["true"],
    )


def _lane_b_binding(cfg: WorkerConfig, write_tree: Path) -> CaptureBinding:
    return CaptureBinding.lane_b(cfg, write_tree)


def _outcome() -> SdkRunOutcome:
    return SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )


def _commit_on_hub(
    repo: Path, dispatch_id: str, rel_path: str, content: str
) -> str:
    name, email = dispatch_git_identity(dispatch_id)
    path = repo / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _git("add", rel_path, cwd=repo)
    env = dict(os.environ)
    env["GIT_COMMITTER_NAME"] = name
    env["GIT_COMMITTER_EMAIL"] = email
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "commit",
            "-m",
            f"hub {dispatch_id}",
            "--author",
            f"{name} <{email}>",
        ],
        check=True,
        capture_output=True,
        env=env,
    )
    return _git("rev-parse", "HEAD", cwd=repo).stdout.strip()


def _settle_lane_b(
    *,
    source_repo: Path,
    binding: CaptureBinding,
    dispatch_id: str,
    files_outside_repo: tuple[str, ...] = (),
) -> tuple[bool | None, str | None, int | None, str | None]:
    fields = settle_lane_and_dispatch_fields(
        binding=binding,
        dispatch_id=dispatch_id,
        write_tree=binding.write_tree,
        receipt_tree=binding.receipt_tree,
        repo_change_set=ChangeSet(created=(), modified=(), deleted=()),
        outcome=_outcome(),
        deviations=[],
        divergence_reason=None,
        baseline=None,
        files_untracked_or_ignored=(),
        offgit_uris=(),
        thread_id="t-landed-resolution",
        gate_d_created_rels=(),
        files_outside_repo=files_outside_repo,
    )
    _lane, _branch, _branch_point, head_sha, commits_ahead, _unfiltered, landed, *_rest = (
        fields
    )
    landed_resolution_reason = fields[-1]
    return landed, head_sha, commits_ahead, landed_resolution_reason


def test_ac2_hub_master_commit_reports_landed_true_11616_shape(
    source_repo: Path, tmp_path: Path
) -> None:
    """AC2 — hub master commit with lane meter 0 → landed true + recovered head_sha."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "auto-11616-shape"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    branch_point = resolve_master_branch_point(source_repo)
    hub_rel = "services/hub_master_fix.py"
    hub_sha = _commit_on_hub(source_repo, dispatch_id, hub_rel, "fix\n")
    branch = f"cursor-sdk/lane-{dispatch_id}"
    state = branch_state(
        source_repo,
        branch_name=branch,
        branch_point=branch_point,
    )
    assert state.commits_ahead == 0
    assert state.head_sha == branch_point

    cfg = _cfg(source_repo, worktree_root)
    binding = _lane_b_binding(cfg, wt)
    landed, head_sha, commits_ahead, reason = _settle_lane_b(
        source_repo=source_repo,
        binding=binding,
        dispatch_id=dispatch_id,
        files_outside_repo=(hub_rel,),
    )
    assert landed is True
    assert head_sha == hub_sha
    assert head_sha != branch_point
    assert commits_ahead is not None and commits_ahead >= 1
    assert reason == HUB_MASTER_HEAD_RECOVERED


def test_ac3_noop_dispatch_reports_landed_false(
    source_repo: Path, tmp_path: Path
) -> None:
    """AC3 falsifier — dispatch that commits nothing must stay landed=false."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "auto-noop-shape"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    branch = f"cursor-sdk/lane-{dispatch_id}"
    branch_point = resolve_master_branch_point(source_repo)
    state = branch_state(
        source_repo,
        branch_name=branch,
        branch_point=branch_point,
    )
    assert state.commits_ahead == 0
    assert state.head_sha == branch_point

    cfg = _cfg(source_repo, worktree_root)
    binding = _lane_b_binding(cfg, wt)
    landed, head_sha, commits_ahead, reason = _settle_lane_b(
        source_repo=source_repo,
        binding=binding,
        dispatch_id=dispatch_id,
        files_outside_repo=(),
    )
    assert landed is False
    assert head_sha == branch_point
    assert commits_ahead == 0
    assert reason is None

    landed2, head_sha2, commits_ahead2, reason2 = _settle_lane_b(
        source_repo=source_repo,
        binding=binding,
        dispatch_id=dispatch_id,
        files_outside_repo=("services/phantom_hub_write.py",),
    )
    assert landed2 is False
    assert head_sha2 == branch_point
    assert commits_ahead2 == 0
    assert reason2 == HUB_MASTER_HEAD_RECOVERY_NOT_FOUND


def test_ac3_unmerged_lane_branch_stays_not_landed_11611_shape(
    source_repo: Path, tmp_path: Path
) -> None:
    """AC3 — work on unmerged lane branch must not become landed via recovery."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "auto-11611-shape"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    branch = f"cursor-sdk/lane-{dispatch_id}"
    branch_point = resolve_master_branch_point(source_repo)
    (wt / "lane_only.py").write_text("lane\n", encoding="utf-8")
    commit_on_terminal(
        dispatch_id=dispatch_id,
        worktree_path=wt,
        branch_name=branch,
    )
    state = branch_state(
        source_repo,
        branch_name=branch,
        branch_point=branch_point,
    )
    assert state.commits_ahead is not None and state.commits_ahead >= 1

    cfg = _cfg(source_repo, worktree_root)
    binding = _lane_b_binding(cfg, wt)
    landed, head_sha, commits_ahead, reason = _settle_lane_b(
        source_repo=source_repo,
        binding=binding,
        dispatch_id=dispatch_id,
        files_outside_repo=(),
    )
    assert landed is False
    assert head_sha == state.head_sha
    assert commits_ahead == state.commits_ahead
    assert reason is None


def test_ac4_lane_branch_advance_unchanged_11618_shape(
    source_repo: Path, tmp_path: Path
) -> None:
    """AC4 — commits_ahead>=1 on lane with ancestry on master still lands via existing path."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "auto-11618-shape"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    branch = f"cursor-sdk/lane-{dispatch_id}"
    branch_point = resolve_master_branch_point(source_repo)
    (wt / "lane_advance.py").write_text("advance\n", encoding="utf-8")
    commit_on_terminal(
        dispatch_id=dispatch_id,
        worktree_path=wt,
        branch_name=branch,
    )
    _git("checkout", "master", cwd=source_repo)
    _git("merge", "--ff-only", branch, cwd=source_repo)
    state = branch_state(
        source_repo,
        branch_name=branch,
        branch_point=branch_point,
    )
    assert state.commits_ahead is not None and state.commits_ahead >= 1

    cfg = _cfg(source_repo, worktree_root)
    binding = _lane_b_binding(cfg, wt)
    landed, head_sha, commits_ahead, reason = _settle_lane_b(
        source_repo=source_repo,
        binding=binding,
        dispatch_id=dispatch_id,
        files_outside_repo=(),
    )
    assert landed is True
    assert head_sha == state.head_sha
    assert commits_ahead == state.commits_ahead
    assert reason is None


def test_ac2_ac3_ac4_subprocess_exit_codes() -> None:
    """AC6 — quote invocation and observed exit codes for the three AC cases."""
    test_path = str(
        Path(__file__).resolve().parent / "test_lane_b_hub_master_landed_resolution.py"
    )
    cases = (
        "test_ac2_hub_master_commit_reports_landed_true_11616_shape",
        "test_ac3_noop_dispatch_reports_landed_false",
        "test_ac3_unmerged_lane_branch_stays_not_landed_11611_shape",
        "test_ac4_lane_branch_advance_unchanged_11618_shape",
    )
    for case in cases:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", test_path, "-q", "-k", case],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
