"""Resolve agent-bus thread to Lane-B worktree root for fs(thread=...)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fs_roots import project_root_path
from tools.agent_bus._shared import relay

WORKTREE_ROOT_DIRNAME = os.environ.get(
    "LANE_WORKTREE_ROOT_DIRNAME", "ulg-arc-worktrees"
)
GATEWAY_REPO_DIRNAME = "universal-llm-gateway"


class LaneBranchResolutionError(Exception):
    """Raised when a thread cannot be resolved to a usable worktree root."""


def branch_for_thread(thread_id: str) -> str:
    """Read the lane's current branch from agent-bus."""
    result = relay(
        "agent-bus",
        "GET",
        f"/threads/{thread_id}/branch-current",
    )
    if "error" in result:
        raise LaneBranchResolutionError(
            f"thread {thread_id!r}: agent-bus error: {result['error']}"
        )
    state = result.get("state")
    if state != "associated":
        raise LaneBranchResolutionError(
            f"thread {thread_id!r}: branch association state={state!r}, "
            "expected 'associated'"
        )
    branch = result.get("current_branch")
    if not branch:
        raise LaneBranchResolutionError(
            f"thread {thread_id!r}: branch association state={state!r} but "
            "current_branch is empty"
        )
    return str(branch)


def _parse_worktree_porcelain(text: str) -> list[dict[str, str | None]]:
    records: list[dict[str, str | None]] = []
    current: dict[str, str | None] = {}
    for line in text.splitlines():
        if not line.strip():
            if current:
                records.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            current["worktree"] = line[len("worktree ") :]
        elif line.startswith("branch "):
            ref = line[len("branch ") :]
            if ref.startswith("refs/heads/"):
                current["branch"] = ref[len("refs/heads/") :]
            else:
                current["branch"] = ref
    if current:
        records.append(current)
    return records


def worktree_dirname_for_branch(branch: str) -> str:
    """Return worktree directory basename for *branch* via git porcelain."""
    repo = project_root_path() / GATEWAY_REPO_DIRNAME
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise LaneBranchResolutionError(
            f"branch {branch!r}: git worktree list failed: {exc}"
        ) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise LaneBranchResolutionError(
            f"branch {branch!r}: git worktree list exited {proc.returncode}: {detail}"
        )
    for record in _parse_worktree_porcelain(proc.stdout):
        if record.get("branch") == branch and record.get("worktree"):
            return os.path.basename(record["worktree"])
    raise LaneBranchResolutionError(
        f"branch {branch!r}: no matching worktree in git porcelain output"
    )


def root_for_thread(thread_id: str) -> Path:
    """Compose thread → branch → worktree dirname → verified root path."""
    branch = branch_for_thread(thread_id)
    dirname = worktree_dirname_for_branch(branch)
    candidate = project_root_path() / WORKTREE_ROOT_DIRNAME / dirname
    if not candidate.is_dir():
        raise LaneBranchResolutionError(
            f"thread {thread_id!r}: resolved worktree root {candidate} is not a directory"
        )
    return candidate


@contextmanager
def bind_lane_worktree_root(root: Path) -> Iterator[Path]:
    """Bind project path resolution to an explicit workspaces root."""
    import tools._project_paths as paths_mod
    import tools.markdown_tool as markdown_mod
    import tools.project as project_mod

    old_project = project_mod._PROJECT_ROOT
    old_paths = paths_mod._PROJECT_ROOT
    old_markdown = markdown_mod._PROJECT_ROOT
    project_mod._PROJECT_ROOT = root
    paths_mod._PROJECT_ROOT = root
    markdown_mod._PROJECT_ROOT = root
    try:
        yield root
    finally:
        project_mod._PROJECT_ROOT = old_project
        paths_mod._PROJECT_ROOT = old_paths
        markdown_mod._PROJECT_ROOT = old_markdown
