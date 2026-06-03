"""Resolve agent-supplied paths inside the mounted PROJECT_ROOT.

Shared by quality_gate, project file tools, and fs workspaces dispatch so
repo-relative refs (``routes/foo.py``) and fully-qualified workspaces paths
(``universal-llm-gateway/libs/...``) resolve to the same on-disk file.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

_PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "/data/project"))


def project_root() -> Path:
    return _PROJECT_ROOT.resolve()


def normalize_directory_arg(directory: str) -> str:
    """Map ``.`` / ``./`` to repo-root listing (empty string)."""
    clean = directory.strip()
    if clean in {"", ".", "./"}:
        return ""
    return clean.lstrip("/")


def candidate_paths(file: str, root: Path | None = None) -> list[Path]:
    """Return candidate absolute paths for a relative or absolute *file* ref."""
    root = (root or project_root()).resolve()
    candidates: list[Path] = []
    seen: set[Path] = set()
    input_path = Path(file)
    repos = repo_roots(root)

    def add(candidate: Path) -> None:
        normalized = candidate.resolve() if candidate.exists() else candidate
        if normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)

    if input_path.is_absolute():
        add(input_path)
    else:
        add(root / file)

    parts = path_parts_without_anchor(input_path)
    if parts and parts[0] == root.name:
        add(root.joinpath(*parts[1:]))
    for repo in repos:
        if input_path.is_absolute():
            add_path_from_named_prefix(parts, repo, add)
        else:
            add(repo / file)
            if parts and parts[0] == repo.name:
                add(repo.joinpath(*parts[1:]))

    return candidates


def resolve_existing_file(file: str, root: Path | None = None) -> Path | None:
    """First existing file among *candidate_paths*, or None."""
    for candidate in candidate_paths(file, root):
        if candidate.is_file():
            return candidate
    return None


def resolve_existing_files(files: list[str], root: Path | None = None) -> list[str]:
    """Resolve agent paths to existing absolute paths (quality_gate helper)."""
    root = (root or project_root()).resolve()
    existing: list[str] = []
    for file in files:
        resolved = resolve_existing_file(file, root)
        if resolved is not None:
            existing.append(str(resolved))
    return existing


def workspaces_relative(path: Path, root: Path | None = None) -> str:
    """Return path relative to PROJECT_ROOT for fs read/list responses."""
    root = (root or project_root()).resolve()
    return str(path.resolve().relative_to(root))


def repo_roots(root: Path) -> list[Path]:
    if (root / ".git").exists():
        return [root]
    try:
        children = [child for child in sorted(root.iterdir()) if child.is_dir()]
    except FileNotFoundError:
        return [root]
    repos = [child for child in children if (child / ".git").exists()]
    if not repos:
        repos = [child for child in children if not child.name.startswith(".")]
    return repos or [root]


def path_parts_without_anchor(path: Path) -> list[str]:
    return [part for part in path.parts if part not in {path.anchor, ""}]


def add_path_from_named_prefix(
    parts: list[str],
    repo: Path,
    add: Callable[[Path], None],
) -> None:
    if repo.name not in parts:
        return
    repo_name_index = parts.index(repo.name)
    add(repo.joinpath(*parts[repo_name_index + 1 :]))


def multi_repo_root_unscoped(root: Path | None = None) -> bool:
    """True when PROJECT_ROOT holds multiple repos and no repo prefix is given."""
    root = (root or project_root()).resolve()
    return len(repo_roots(root)) > 1
