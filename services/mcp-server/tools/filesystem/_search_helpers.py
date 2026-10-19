"""Workspaces find helpers — worktree path filtering before result caps."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from tools.filesystem._ops_search import DEFAULT_MAX_RESULTS

WORKTREE_PATH_PARTS = frozenset({".worktrees", "ulg-arc-worktrees"})


def path_contains_worktree_segment(rel_path: str) -> bool:
    return any(part in WORKTREE_PATH_PARTS for part in Path(rel_path).parts)


def path_has_worktree_below_scope(rel_path: str, scope_dir: str) -> bool:
    """True when a worktree segment appears below *scope_dir*, not within it."""
    rel_parts = Path(rel_path).parts
    scope_parts = Path(scope_dir).parts if scope_dir else ()
    if scope_parts and any(part in WORKTREE_PATH_PARTS for part in scope_parts):
        return False
    if scope_parts and rel_parts[: len(scope_parts)] != scope_parts:
        return path_contains_worktree_segment(rel_path)
    suffix = rel_parts[len(scope_parts) :]
    return any(part in WORKTREE_PATH_PARTS for part in suffix)


def filter_find_matches(
    matches: list[str], *, scope_dir: str, include_worktrees: bool
) -> list[str]:
    if include_worktrees:
        return matches
    return [
        m
        for m in matches
        if not path_has_worktree_below_scope(m, scope_dir)
    ]


def find_workspaces_files(
    *,
    pattern: str,
    directory: str = "",
    include_worktrees: bool = False,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    """Glob filename find under workspaces with worktree exclusion before truncation."""
    from tools import project as project_mod
    from tools.project import (
        _filesystem_listing,
        _safe_project_path,
        multi_repo_root_unscoped,
        normalize_directory_arg,
        workspaces_relative,
    )

    project_root = project_mod._PROJECT_ROOT.resolve()
    directory = normalize_directory_arg(directory)
    if multi_repo_root_unscoped(project_root) and not directory:
        return {
            "error": (
                "find at workspaces root without a repo prefix is too broad. "
                "Scope path to a repo, e.g. path='universal-llm-gateway'."
            ),
            "hint": (
                "fs(op='find', sandbox='workspaces', "
                "path='universal-llm-gateway', content='session_handoff.py')"
            ),
        }
    glob_pat = pattern if any(ch in pattern for ch in "*?[]") else f"*{pattern}*"
    if directory:
        target = _safe_project_path(directory)
        if not target.is_dir():
            if target.is_file():
                rel = workspaces_relative(target, project_root)
                matched = fnmatch.fnmatch(target.name, glob_pat) or fnmatch.fnmatch(
                    rel, glob_pat
                )
                paths = filter_find_matches(
                    [rel] if matched else [],
                    scope_dir=directory,
                    include_worktrees=include_worktrees,
                )
                return {
                    "path": directory,
                    "mode": "file",
                    "matches": paths,
                    "truncated": False,
                    "pattern": glob_pat,
                }
            raise ValueError(f"Path is not a directory: {directory!r}")

    files, _, _ = _filesystem_listing(
        directory, max_depth=None, skip_binary=False, cap=None
    )
    matches: list[str] = []
    truncated = False
    for rel_path in files:
        if path_has_worktree_below_scope(rel_path, directory) and not include_worktrees:
            continue
        base = Path(rel_path).name
        if fnmatch.fnmatch(base, glob_pat) or fnmatch.fnmatch(rel_path, glob_pat):
            matches.append(rel_path)
            if len(matches) >= max_results:
                truncated = True
                break
    return {
        "path": directory,
        "mode": "directory",
        "matches": matches,
        "match_count": len(matches),
        "truncated": truncated,
        "pattern": glob_pat,
    }
