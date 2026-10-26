"""Locate a hub git checkout when ``WORKSPACE_ROOT`` has no ``.git`` (container copy)."""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

from universal_workspace import get_workspace_root

_GIT_TIMEOUT_S = 8.0


def _has_git_dir(root: Path) -> bool:
    return (root / ".git").exists()


def resolve_hub_git_repo(from_repo: Path) -> Path:
    """Return a hub checkout path for ancestry probes from a lane or container root."""
    repo = from_repo.resolve()
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return repo
    if proc.returncode != 0:
        return repo
    lines = proc.stdout.splitlines()
    first_worktree: Path | None = None
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if not line.startswith("worktree "):
            idx += 1
            continue
        wt_path = Path(line.split(" ", 1)[1].strip())
        if first_worktree is None:
            first_worktree = wt_path
        idx += 1
        while idx < len(lines) and not lines[idx].startswith("worktree "):
            if lines[idx].startswith("branch refs/heads/master"):
                return wt_path
            idx += 1
    return first_worktree or repo


@lru_cache(maxsize=1)
def git_resolution_roots() -> tuple[Path, ...]:
    """Ordered git roots to try when resolving symbolic ``code_ref`` values."""
    ordered: list[Path] = []
    seen: set[Path] = set()

    def _add(path: Path) -> None:
        resolved = path.resolve()
        if resolved in seen:
            return
        if not _has_git_dir(resolved):
            return
        seen.add(resolved)
        ordered.append(resolved)

    try:
        workspace = get_workspace_root()
    except RuntimeError:
        workspace = None
    if workspace is not None:
        _add(workspace)
        _add(resolve_hub_git_repo(workspace))
    return tuple(ordered)


__all__ = ["git_resolution_roots", "resolve_hub_git_repo"]
