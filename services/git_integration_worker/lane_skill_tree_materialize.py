"""Mint-time hub skill-tree materialization for lane-B worktrees.

Git worktrees inherit tracked ``.cursor/skills`` from the branch tip but cannot
carry gitignored ``.claude/skills`` (life_local SOTs). Hub checkout also often
has hub-only ``.cursor/skills`` bodies that never landed on the branch point.
Copy the hub working-tree skill directories from ``GIT_INTEGRATION_SOURCE_REPO``
so ``load_skill_catalog(validate_sot=True)`` matches hub parity.
"""

from __future__ import annotations

import shutil
from pathlib import Path

_SKILL_TREE_ROOTS: tuple[tuple[str, ...], ...] = (
    (".cursor", "skills"),
    (".claude", "skills"),
)


def materialize_hub_skill_trees_for_lane(
    *,
    hub_root: Path,
    lane_root: Path,
) -> None:
    """Mirror hub working-tree skill SOT directories into ``lane_root``."""
    hub = hub_root.resolve()
    lane = lane_root.resolve()
    for parts in _SKILL_TREE_ROOTS:
        src_base = hub.joinpath(*parts)
        if not src_base.is_dir():
            continue
        dst_base = lane.joinpath(*parts)
        dst_base.mkdir(parents=True, exist_ok=True)
        for child in src_base.iterdir():
            if not child.is_dir() or child.name == "README":
                continue
            if not (child / "SKILL.md").is_file():
                continue
            dest = dst_base / child.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(child, dest)
