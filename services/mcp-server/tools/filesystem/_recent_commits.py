"""fs(op=recent_commits) — oneline git history, no diffs.

Workspaces-only catch-up query. Refuses ``.git`` path reads; history goes
through this op, not raw object-store browsing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from git_integrate.events import emit_git_log_read
from git_integrate.recent_commits import DEFAULT_N, MAX_N, log_oneline

from tools._project_paths import candidate_paths, project_root, repo_roots


def path_touches_git_dir(rel: str) -> bool:
    """True when any path component is ``.git`` (object-store browse)."""
    return any(part == ".git" for part in Path(rel).parts)


def find_git_repo(start: Path, *, stop_at: Path) -> Path | None:
    """Walk *start* toward *stop_at* inclusive looking for a ``.git`` dir."""
    cur = start.resolve()
    if cur.is_file():
        cur = cur.parent
    stop = stop_at.resolve()
    for path in [cur, *cur.parents]:
        if (path / ".git").exists():
            return path
        if path == stop:
            break
    return None


def resolve_repo(rel: str) -> Path | None:
    """Resolve the git repo for a workspaces-relative *rel* (never above root)."""
    root = project_root()
    if not rel.strip():
        repos = [p for p in repo_roots(root) if (p / ".git").exists()]
        for repo in repos:
            if repo.name == "universal-llm-gateway":
                return repo
        if repos:
            return repos[0]
        return find_git_repo(root, stop_at=root)

    for candidate in candidate_paths(rel, root):
        start = candidate.parent if candidate.is_file() else candidate
        if not start.exists():
            continue
        repo = find_git_repo(start, stop_at=root)
        if repo is not None:
            return repo
    return find_git_repo(root / rel, stop_at=root)


def recent_commits_impl(
    *, path: str, since: str = "", limit: int = 0
) -> dict[str, Any]:
    """Dispatch body for ``fs(op=recent_commits)``."""
    if path_touches_git_dir(path):
        return {
            "error": (
                "recent_commits refuses .git path reads; query history via "
                "this op, not .git files"
            )
        }
    repo = resolve_repo(path)
    if repo is None:
        return {"error": "no git repository found for path"}
    n = DEFAULT_N if limit <= 0 else min(limit, MAX_N)
    try:
        result = log_oneline(repo, since=since or None, n=n)
    except ValueError as exc:
        return {"error": str(exc)}
    emit_git_log_read(
        head=str(result.get("head") or ""),
        n=n,
        since=str(result.get("since") or ""),
        truncated=bool(result.get("truncated")),
    )
    return result
