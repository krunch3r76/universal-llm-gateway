"""Collapse doubled ``worktree_root`` segments in managed workspace paths."""

from __future__ import annotations

from pathlib import Path

from services.git_integration_worker.cursor_sdk_worktree import (
    collapse_doubled_worktree_root,
)


def test_collapse_doubled_worktree_root(tmp_path: Path) -> None:
    root = tmp_path / "ulg-arc-worktrees"
    root.mkdir()
    doubled = root / "universal-llm-gateway" / "lane-12498" / root / "universal-llm-gateway" / "lane-12498"
    collapsed = collapse_doubled_worktree_root(doubled, root)
    assert collapsed == (root / "universal-llm-gateway" / "lane-12498").resolve()
