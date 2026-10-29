"""Test-session skill-tree materialization for git-clean land worktrees (a:40580).

Land and detached gate worktrees inherit tracked ``.cursor/skills`` but not
gitignored life/legal bodies. Lane-B mint copies hub trees at runtime; pytest
must do the same before ``load_skill_catalog(validate_sot=True)`` first runs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from services.git_integration_worker.lane_skill_tree_materialize import (
    materialize_hub_skill_trees_for_lane,
)

_GIT_TIMEOUT_S = 30.0


def resolve_hub_worktree_root(lane_root: Path) -> Path | None:
    """Main checkout for this repo: first ``git worktree list`` entry whose ``.git`` is a directory."""
    proc = subprocess.run(
        ["git", "-C", str(lane_root.resolve()), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if not line.startswith("worktree "):
            continue
        candidate = Path(line.split(" ", 1)[1].strip())
        if (candidate / ".git").is_dir():
            return candidate.resolve()
    return None


def _catalog_sot_paths(lane_root: Path) -> list[Path]:
    from claude_bundles.catalog import load_skill_catalog

    catalog = load_skill_catalog(repo_root=lane_root, validate_sot=False)
    paths: list[Path] = []
    for slug, entry in catalog.entries.items():
        dirname = entry.sot_dirname or slug
        if entry.surface_class == "life_local":
            paths.append(lane_root / ".claude" / "skills" / dirname / "SKILL.md")
        elif entry.surface_class in ("shared_sync", "cursor_only"):
            paths.append(lane_root / ".cursor" / "skills" / dirname / "SKILL.md")
    return paths


def skill_sot_missing(lane_root: Path) -> bool:
    """True when any catalog row's on-disk SOT is absent under this worktree."""
    try:
        paths = _catalog_sot_paths(lane_root)
    except OSError:
        return False
    return any(not path.is_file() for path in paths)


def _hub_can_supply_missing(hub_root: Path, lane_root: Path) -> bool:
    """Hub must have at least one SKILL.md for a path missing in the lane tree."""
    try:
        paths = _catalog_sot_paths(lane_root)
    except OSError:
        return False
    for path in paths:
        if path.is_file():
            continue
        rel = path.relative_to(lane_root)
        if (hub_root / rel).is_file():
            return True
    return False


def maybe_materialize_skill_trees_for_pytest(lane_root: Path) -> bool:
    """Copy hub skill trees into ``lane_root`` when catalog SOTs are missing.

    Returns True when ``materialize_hub_skill_trees_for_lane`` ran.
    """
    lane = lane_root.resolve()
    if not skill_sot_missing(lane):
        return False
    hub = resolve_hub_worktree_root(lane)
    if hub is None or not _hub_can_supply_missing(hub, lane):
        return False
    materialize_hub_skill_trees_for_lane(hub_root=hub, lane_root=lane)
    from claude_bundles.catalog import clear_skill_catalog_cache

    clear_skill_catalog_cache()
    return True
