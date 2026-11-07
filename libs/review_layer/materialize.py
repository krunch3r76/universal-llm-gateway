"""Lane worktree paths and review-target prompt blocks (read-only; no file writes)."""

from __future__ import annotations

import os
from pathlib import Path

from review_layer.git_io import (
    ReviewLayerInfraError,
    ReviewTargetUnreachable,
    diff_name_only_z,
    hub_repo_path,
    is_ancestor_of,
    merge_base,
    resolve_commit,
)

_DEFAULT_WORKTREE_ROOT = Path("/mnt/torus/projects/ulg-arc-worktrees")
_REPO_BASENAME = "universal-llm-gateway"

__all__ = [
    "ReviewLayerInfraError",
    "ReviewTargetUnreachable",
    "default_lane_branch",
    "format_review_target_block",
    "lane_worktree_path",
    "resolve_review_target",
    "worktree_root",
]


def worktree_root() -> Path:
    """GIW lane worktree root (``GIT_INTEGRATION_WORKTREE_ROOT``)."""
    raw = os.environ.get("GIT_INTEGRATION_WORKTREE_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _DEFAULT_WORKTREE_ROOT.resolve()


def lane_worktree_path(thread_id: str, root: Path | None = None) -> Path:
    """Path to ``…/ulg-arc-worktrees/<repo>/lane-<thread>``."""
    base = root or worktree_root()
    return base / _REPO_BASENAME / f"lane-{thread_id}"


def default_lane_branch(thread_id: str) -> str:
    return f"cursor-sdk/lane-{thread_id}"


def resolve_review_target(
    *,
    thread_id: str,
    head_ref: str | None,
    base_ref: str | None,
    repo: Path | None = None,
    worktree_root_path: Path | None = None,
) -> dict[str, str] | None:
    """Validate lane reachability; return shas or ``None`` when merged into master."""
    repo = (repo or hub_repo_path()).resolve()
    branch = default_lane_branch(thread_id)
    head = head_ref or branch
    head_sha = resolve_commit(repo, head)
    branch_sha = resolve_commit(repo, branch)

    for master_ref in ("master", "main"):
        try:
            master_sha = resolve_commit(repo, master_ref)
        except ReviewTargetUnreachable:
            continue
        if is_ancestor_of(repo, head_sha, master_sha):
            return None

    if not is_ancestor_of(repo, head_sha, branch_sha):
        raise ReviewTargetUnreachable("head is not on the lane branch")

    wt = lane_worktree_path(thread_id, worktree_root_path)
    if not wt.is_dir():
        raise ReviewTargetUnreachable("lane worktree is not present")

    if base_ref:
        base_tip = resolve_commit(repo, base_ref)
        base_sha = merge_base(repo, base_tip, head_sha)
    else:
        base_sha = None
        for master_ref in ("master", "main"):
            try:
                master_sha = resolve_commit(repo, master_ref)
            except ReviewTargetUnreachable:
                continue
            base_sha = merge_base(repo, master_sha, head_sha)
            break
        if base_sha is None:
            raise ReviewTargetUnreachable("no merge-base against master")

    return {
        "thread": thread_id,
        "branch": branch,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "head_ref": head,
    }


def format_review_target_block(
    *,
    thread_id: str,
    branch: str,
    base_sha: str,
    head_sha: str,
    repo: Path | None = None,
    max_files: int = 50,
) -> str:
    """Appendix instructing API critics to read the lane via ``fs(thread=…)``."""
    repo = repo or hub_repo_path()
    paths = diff_name_only_z(repo, base_sha, head_sha)
    lines = [
        "",
        "## review_target",
        f"thread: {thread_id}",
        f"branch: {branch}",
        f"base_sha: {base_sha}",
        f"head_sha: {head_sha}",
        f"diff: {base_sha}..{head_sha}",
        "",
        "Read lane files via fs(sandbox=\"workspaces\", thread=\"" + thread_id + "\", …).",
        "list, search, and recent_commits accept the same thread=.",
        "",
        "files:",
    ]
    if not paths:
        lines.append("- (no changed paths)")
    else:
        shown = paths[:max_files]
        for rel in shown:
            lines.append(
                f'- fs(sandbox="workspaces", thread="{thread_id}", op="read", '
                f'path="universal-llm-gateway/{rel}")'
            )
        if len(paths) > max_files:
            lines.append(f"- +{len(paths) - max_files} more (use diff range above)")
    lines.append(
        "Hub master does not include this work; use the lane fs calls above, not master checkout."
    )
    return "\n".join(lines) + "\n"
