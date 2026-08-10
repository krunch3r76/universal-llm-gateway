"""Lane-B S3 — commit-on-terminal and non-destructive prune."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    capture_wt_baseline_with_hashes,
    changed_paths,
    prepare_closeout_delivery,
)
from services.git_integration_worker.cursor_sdk_lane_b_commit import (
    SalvageResult,
    branch_state,
    commit_on_terminal,
    salvage_commit,
)
from services.git_integration_worker.cursor_sdk_worktree import (
    maybe_prune_worktree_on_terminal,
    mint_dispatch_worktree,
    prune_dispatch_worktree,
    resolve_master_branch_point,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    lookup_dispatch_worktree,
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


def test_ac_s3_1_terminal_dirty_tree_commits_on_branch(
    source_repo: Path, tmp_path: Path
) -> None:
    """AC-S3.1: dirty Lane-B terminal commits on cursor-sdk/{id}."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "s3-terminal"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    record_rel = "lane_b_touch.py"
    (wt / record_rel).write_text("payload\n", encoding="utf-8")
    cfg = _cfg(source_repo, worktree_root)
    binding = _lane_b_binding(cfg, wt)
    baseline = capture_wt_baseline_with_hashes(wt)
    assert baseline is not None

    delivery = prepare_closeout_delivery(
        source_repo=source_repo,
        binding=binding,
        dispatch_id=dispatch_id,
        outcome=_outcome(),
        degraded_reason=None,
        thread_id="t-s3-1",
        work_item_ref=None,
        baseline=baseline,
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    branch = payload["branch"]
    assert branch == f"cursor-sdk/{dispatch_id}"
    assert payload["commits_ahead"] >= 1
    head_sha = payload["head_sha"]
    assert head_sha == _git("rev-parse", "HEAD", cwd=wt).stdout.strip()
    assert payload["landed"] is False
    # G2 land-discipline: unlanded Lane-B progress cannot grade complete.
    assert payload["status"] == "partial"
    assert "land:lane_b_unlanded" in (payload.get("deviations") or [])


def test_ac_s3_2_prune_recovers_file_from_branch(
    source_repo: Path, tmp_path: Path
) -> None:
    """AC-S3.2: pruned dispatch file Q recoverable from cursor-sdk/{id}."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "s3-recover"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    rel = "Q"
    (wt / rel).write_text("recover-me\n", encoding="utf-8")
    branch = f"cursor-sdk/{dispatch_id}"
    result = maybe_prune_worktree_on_terminal(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )
    assert result.pruned
    assert result.branch_retained
    assert not wt.exists()
    show = subprocess.run(
        ["git", "-C", str(source_repo), "show", f"{branch}:{rel}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert show.returncode == 0
    assert show.stdout.strip() == "recover-me"


def test_ac_s3_3_unmerged_branch_retained_after_prune(
    source_repo: Path, tmp_path: Path
) -> None:
    """AC-S3.3: unmerged branch survives prune."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "s3-retain"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    branch = f"cursor-sdk/{dispatch_id}"
    (wt / "keep.py").write_text("x\n", encoding="utf-8")
    result = prune_dispatch_worktree(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )
    assert result.pruned
    assert result.branch_retained
    listed = _git("branch", "--list", branch, cwd=source_repo).stdout
    assert branch in listed


def test_ac_s3_4_merged_branch_deleted_after_prune(
    source_repo: Path, tmp_path: Path
) -> None:
    """AC-S3.4: merged branch is removed on prune."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "s3-merged"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    branch = f"cursor-sdk/{dispatch_id}"
    (wt / "merged.py").write_text("merged\n", encoding="utf-8")
    _git("add", "-A", cwd=wt)
    _git("commit", "-m", "lane b work", cwd=wt)
    _git("checkout", "master", cwd=source_repo)
    _git("merge", branch, cwd=source_repo)
    result = prune_dispatch_worktree(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )
    assert result.pruned
    assert not result.branch_retained
    listed = _git("branch", "--list", branch, cwd=source_repo).stdout.strip()
    assert listed == ""


def test_ac_s3_5_capture_precedes_commit_same_file_lists(
    source_repo: Path, tmp_path: Path
) -> None:
    """AC-S3.5: porcelain capture identical whether commit-on-terminal runs."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "s3-order"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    (wt / "order.py").write_text("v1\n", encoding="utf-8")
    cfg = _cfg(source_repo, worktree_root)
    binding = _lane_b_binding(cfg, wt)
    baseline = capture_wt_baseline_with_hashes(wt)
    assert baseline is not None
    pre_change, _ = changed_paths(wt, baseline)

    with patch(
        "services.git_integration_worker.cursor_sdk_lane_b_commit.commit_on_terminal",
        return_value=SalvageResult(committed=False, head_sha=None),
    ):
        delivery_no_commit = prepare_closeout_delivery(
            source_repo=source_repo,
            binding=binding,
            dispatch_id=dispatch_id,
            outcome=_outcome(),
            degraded_reason=None,
            thread_id="t-s3-5a",
            work_item_ref=None,
            baseline=baseline,
            deliverables_expected=True,
        )
    payload_no_commit = json.loads(delivery_no_commit.body)

    wt2 = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id="s3-order-b",
    )
    (wt2 / "order.py").write_text("v1\n", encoding="utf-8")
    baseline2 = capture_wt_baseline_with_hashes(wt2)
    assert baseline2 is not None
    binding2 = _lane_b_binding(cfg, wt2)
    delivery_commit = prepare_closeout_delivery(
        source_repo=source_repo,
        binding=binding2,
        dispatch_id="s3-order-b",
        outcome=_outcome(),
        degraded_reason=None,
        thread_id="t-s3-5b",
        work_item_ref=None,
        baseline=baseline2,
        deliverables_expected=True,
    )
    payload_commit = json.loads(delivery_commit.body)

    assert payload_no_commit["files_created"] == payload_commit["files_created"]
    assert payload_no_commit["files_modified"] == payload_commit["files_modified"]
    assert payload_no_commit["files_deleted"] == payload_commit["files_deleted"]
    assert payload_commit["commits_ahead"] >= 1


def test_ac_s3_6_lane_a_never_commits_or_salvages(source_repo: Path) -> None:
    """AC-S3.6: Lane-A closeout path does not commit on shared master."""
    (source_repo / "lane_a.py").write_text("dirty\n", encoding="utf-8")
    baseline = capture_wt_baseline_with_hashes(source_repo)
    assert baseline is not None
    head_before = _git("rev-parse", "HEAD", cwd=source_repo).stdout.strip()
    delivery = prepare_closeout_delivery(
        source_repo=source_repo,
        binding=None,
        dispatch_id="lane-a-no-commit",
        outcome=_outcome(),
        degraded_reason=None,
        thread_id="t-s3-6",
        work_item_ref=None,
        baseline=baseline,
        deliverables_expected=True,
    )
    head_after = _git("rev-parse", "HEAD", cwd=source_repo).stdout.strip()
    assert head_before == head_after
    payload = json.loads(delivery.body)
    assert payload.get("lane") is None
    assert payload.get("commits_ahead") == 0


def test_branch_state_counts_since_branch_point(source_repo: Path, tmp_path: Path) -> None:
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "s3-state"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    branch = f"cursor-sdk/{dispatch_id}"
    branch_point = resolve_master_branch_point(source_repo)
    (wt / "a.py").write_text("a\n", encoding="utf-8")
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
    assert state.commits_ahead >= 1
    assert state.head_sha == _git("rev-parse", branch, cwd=source_repo).stdout.strip()
    assert not state.merged_into_master


def _install_refusing_hook(repo: Path, marker: str = "hook-refused-marker") -> None:
    """Install a pre-commit hook that rejects every commit in repo and worktrees."""
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho '{marker}' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)


def test_salvage_reports_refusal_distinctly_from_clean_tree(
    source_repo: Path, tmp_path: Path
) -> None:
    """A hook-rejected commit is `refused`, not the clean-tree no-op."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "s3-refused"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    clean = salvage_commit(wt, message="clean")
    assert not clean.committed
    assert not clean.refused

    _install_refusing_hook(source_repo)
    (wt / "at_risk.py").write_text("only copy\n", encoding="utf-8")
    refused = salvage_commit(wt, message="dirty")
    assert not refused.committed
    assert refused.refused
    assert "hook-refused-marker" in (refused.error or "")
    assert "\n" not in refused.short_error


def test_prune_fails_closed_when_salvage_refused(
    source_repo: Path, tmp_path: Path
) -> None:
    """Unsalvageable work is never force-removed — the worktree is the only copy."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "s3-failclosed"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    branch = f"cursor-sdk/{dispatch_id}"
    _install_refusing_hook(source_repo)
    (wt / "at_risk.py").write_text("only copy\n", encoding="utf-8")

    result = prune_dispatch_worktree(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )
    assert not result.pruned
    assert result.salvage_refused
    assert result.branch_retained
    assert (wt / "at_risk.py").read_text(encoding="utf-8") == "only copy\n"
    assert branch in _git("branch", "--list", branch, cwd=source_repo).stdout
    assert lookup_dispatch_worktree(dispatch_id=dispatch_id) is not None


def test_prune_retains_dirty_empty_branch_when_salvage_does_not_commit(
    source_repo: Path, tmp_path: Path,
) -> None:
    """Dirty work on commits_ahead=0 must retain worktree even if salvage is a no-op."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "s3-dirty-empty"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    branch = f"cursor-sdk/{dispatch_id}"
    (wt / "at_risk.py").write_text("only copy\n", encoding="utf-8")

    with patch(
        "services.git_integration_worker.cursor_sdk_worktree_prune.salvage_commit",
        return_value=SalvageResult(committed=False, head_sha=None, refused=False),
    ):
        result = prune_dispatch_worktree(
            dispatch_id=dispatch_id,
            source_repo=source_repo,
        )

    assert not result.pruned
    assert result.salvage_refused
    assert result.branch_retained
    assert (wt / "at_risk.py").read_text(encoding="utf-8") == "only copy\n"
    assert branch in _git("branch", "--list", branch, cwd=source_repo).stdout
    assert lookup_dispatch_worktree(dispatch_id=dispatch_id) is not None


def _commit_branch_file(
    source_repo: Path, worktree_root: Path, *, dispatch_id: str, name: str, body: str
) -> tuple[str, str]:
    """Mint a worktree, write one file, commit it; return (branch, branch_point)."""
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    branch = f"cursor-sdk/{dispatch_id}"
    branch_point = resolve_master_branch_point(source_repo)
    (wt / name).write_text(body, encoding="utf-8")
    commit_on_terminal(
        dispatch_id=dispatch_id,
        worktree_path=wt,
        branch_name=branch,
    )
    return branch, branch_point


def test_content_landed_branch_is_reclaimable(
    source_repo: Path, tmp_path: Path
) -> None:
    """Work that reached master by cherry-pick is landed, though ancestry denies it."""
    branch, branch_point = _commit_branch_file(
        source_repo,
        tmp_path / "worktrees",
        dispatch_id="s3-cherry",
        name="landed.py",
        body="shipped\n",
    )
    tip = _git("rev-parse", branch, cwd=source_repo).stdout.strip()
    # Master moves on first, so the replay lands under a genuinely different SHA.
    (source_repo / "unrelated.md").write_text("moved on\n", encoding="utf-8")
    _git("add", "unrelated.md", cwd=source_repo)
    _git("commit", "-m", "unrelated master work", cwd=source_repo)
    _git("cherry-pick", tip, cwd=source_repo)
    master_tip = _git("rev-parse", "master", cwd=source_repo).stdout.strip()
    assert master_tip != tip

    state = branch_state(
        source_repo,
        branch_name=branch,
        branch_point=branch_point,
    )
    assert not state.merged_into_master
    assert state.content_landed
    assert state.safe_to_delete


def test_unique_work_branch_is_never_reclaimed(
    source_repo: Path, tmp_path: Path
) -> None:
    """A branch holding work absent from master stays retained."""
    branch, branch_point = _commit_branch_file(
        source_repo,
        tmp_path / "worktrees",
        dispatch_id="s3-unique",
        name="only_copy.py",
        body="not on master\n",
    )
    state = branch_state(
        source_repo,
        branch_name=branch,
        branch_point=branch_point,
    )
    assert state.commits_ahead >= 1
    assert not state.merged_into_master
    assert not state.content_landed
    assert not state.safe_to_delete


def test_zero_commit_branch_is_empty_not_merged(
    source_repo: Path, tmp_path: Path
) -> None:
    """`git branch --merged` lists a never-committed branch; that is not merged work."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "s3-empty"
    mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    branch = f"cursor-sdk/{dispatch_id}"
    merged_listing = _git("branch", "--merged", "master", cwd=source_repo).stdout
    assert branch in merged_listing

    state = branch_state(
        source_repo,
        branch_name=branch,
        branch_point=resolve_master_branch_point(source_repo),
    )
    assert state.is_empty
    assert not state.merged_into_master
    assert state.safe_to_delete


def test_lane_b_commit_refusal_blocks_shipped_grade(
    source_repo: Path, tmp_path: Path
) -> None:
    """Closeout cannot grade shipped off a tree git refused to commit."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "s3-grade"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    (wt / "graded.py").write_text("payload\n", encoding="utf-8")
    baseline = capture_wt_baseline_with_hashes(wt)
    assert baseline is not None
    _install_refusing_hook(source_repo)

    delivery = prepare_closeout_delivery(
        source_repo=source_repo,
        binding=_lane_b_binding(_cfg(source_repo, worktree_root), wt),
        dispatch_id=dispatch_id,
        outcome=_outcome(),
        degraded_reason=None,
        thread_id="t-s3-grade",
        work_item_ref=None,
        baseline=baseline,
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert payload["commits_ahead"] == 0
    assert payload["work_outcome"] != "shipped"
    assert any(
        str(token).startswith("divergence:lane_b_commit_refused")
        for token in payload.get("deviations") or []
    )
