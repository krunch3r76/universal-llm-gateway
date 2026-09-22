"""Collapse doubled ``worktree_root`` segments in managed workspace paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.git_integration_worker import cursor_sdk_worktree as wt_mod
from services.git_integration_worker.cursor_sdk_worktree import (
    collapse_doubled_worktree_root,
)


def _doubled_lane_path(root: Path, lane: str = "lane-12498") -> Path:
    """Build a resolved path whose text still embeds ``worktree_root`` twice."""
    root = root.resolve()
    root_s = str(root)
    doubled = Path(
        f"{root_s}/universal-llm-gateway/{lane}/{root_s}/universal-llm-gateway/{lane}"
    )
    doubled.mkdir(parents=True, exist_ok=True)
    return doubled


@pytest.fixture
def collapse_observations(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture collapse observations without publishing to the event bus."""
    emitted: list[dict[str, Any]] = []

    def _capture(**kwargs: Any) -> None:
        emitted.append(kwargs)

    monkeypatch.setattr(wt_mod, "_emit_worktree_root_collapsed", _capture)
    return emitted


def test_collapse_doubled_worktree_root(
    tmp_path: Path, collapse_observations: list[dict[str, Any]]
) -> None:
    root = tmp_path / "ulg-arc-worktrees"
    root.mkdir()
    doubled = _doubled_lane_path(root)
    collapsed = collapse_doubled_worktree_root(
        doubled,
        root,
        call_site="test_collapse_doubled_worktree_root",
    )
    assert collapsed == (root / "universal-llm-gateway" / "lane-12498").resolve()
    assert len(collapse_observations) == 1
    obs = collapse_observations[0]
    assert obs["call_site"] == "test_collapse_doubled_worktree_root"
    assert obs["path_after"] == str(collapsed)
    assert str(root.resolve()) in obs["path_before"]


def test_collapse_without_doubling_emits_nothing(
    tmp_path: Path, collapse_observations: list[dict[str, Any]]
) -> None:
    root = tmp_path / "ulg-arc-worktrees"
    root.mkdir()
    normal = root / "universal-llm-gateway" / "lane-1"
    normal.mkdir(parents=True)
    result = collapse_doubled_worktree_root(
        normal,
        root,
        call_site="test_no_collapse",
    )
    assert result == normal.resolve()
    assert collapse_observations == []


def test_collapse_fires_observation_emit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails if a collapse runs without calling the observation emitter."""
    root = tmp_path / "arc-root"
    root.mkdir()
    doubled = _doubled_lane_path(root, lane="lane")
    emitted: list[dict[str, Any]] = []

    def _must_emit(**kwargs: Any) -> None:
        emitted.append(kwargs)

    monkeypatch.setattr(wt_mod, "_emit_worktree_root_collapsed", _must_emit)
    collapse_doubled_worktree_root(
        doubled,
        root,
        call_site="mint_dispatch_worktree",
        dispatch_id="d-1",
        thread_id="12498",
    )
    assert len(emitted) == 1
    assert emitted[0]["call_site"] == "mint_dispatch_worktree"
    assert emitted[0]["dispatch_id"] == "d-1"
    assert emitted[0]["thread_id"] == "12498"
