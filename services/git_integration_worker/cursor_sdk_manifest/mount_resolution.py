"""WORKSPACES_ROOT mount, registered repo roots, and mount-path classification.

Mirrors MCP ``project_root()`` / ``_project_paths.repo_roots`` without importing
mcp-server. Invariant: ``classify_mount_path`` returns one of the five literals
in its return annotation and never raises on paths outside the mount (it
returns ``outside_mount``). This module is an intra-package leaf — it calls
only its own helpers. ``registered_repo_roots`` is re-exported; the reverse
lazy import from ``cursor_sdk_outside_census`` is why ``__init__`` places the
``snapshot_outside_repo_paths`` pass-through last.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

def workspaces_mount_root() -> Path:
    """WORKSPACES_ROOT mount — mirrors MCP ``project_root()`` for repo enumeration."""
    return Path(os.environ.get("WORKSPACES_ROOT", "/mnt/torus/projects")).resolve()


def resolve_mount_root(source_repo: Path) -> Path:
    """Prefer configured WORKSPACES_ROOT when *source_repo* lives under it."""
    repo = source_repo.resolve()
    configured = workspaces_mount_root()
    try:
        repo.relative_to(configured)
        return configured
    except ValueError:
        if repo.name == "universal-llm-gateway":
            return repo.parent
        return repo


def registered_repo_roots(mount_root: Path | None = None) -> list[Path]:
    """Mirror ``_project_paths.repo_roots`` without importing mcp-server."""
    root = (mount_root or workspaces_mount_root()).resolve()
    if (root / ".git").exists():
        return [root]
    try:
        children = [child for child in sorted(root.iterdir()) if child.is_dir()]
    except FileNotFoundError:
        return [root]
    repos = [child for child in children if (child / ".git").exists()]
    if not repos:
        repos = [child for child in children if not child.name.startswith(".")]
    return [child.resolve() for child in (repos or [root])]


def mount_relative_path(mount_root: Path, path: Path) -> str | None:
    try:
        return str(path.resolve().relative_to(mount_root.resolve()))
    except ValueError:
        return None
def classify_mount_path(
    path: Path,
    *,
    source_repo: Path,
    mount_root: Path,
    repo_roots: list[Path] | None = None,
) -> Literal[
    "source_repo", "other_repo", "shared_cursor", "unknown_root_child", "outside_mount"
]:
    resolved = path.resolve()
    rel = mount_relative_path(mount_root, resolved)
    if rel is None:
        return "outside_mount"
    if rel == ".cursor" or rel.startswith(".cursor/"):
        return "shared_cursor"
    roots = repo_roots or registered_repo_roots(mount_root)
    source_resolved = source_repo.resolve()
    for repo in roots:
        try:
            resolved.relative_to(repo.resolve())
        except ValueError:
            continue
        if repo.resolve() == source_resolved:
            return "source_repo"
        return "other_repo"
    return "unknown_root_child"
