"""Tests for lane-A tree residue derivation and authored-path probe."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
    derive_tree_residue,
    inject_tree_residue_line,
    probe_authored_path_baseline,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    capture_wt_baseline_with_hashes,
)

pytestmark = pytest.mark.offline


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_probe_records_baseline_limits() -> None:
    probe = probe_authored_path_baseline()
    assert probe.exact_at_dispatch is True
    assert probe.covers_nested_cursor_sdk is True
    assert probe.covers_attended_composer is False
    assert "wt_baseline" in probe.detail


def test_tree_residue_excludes_authored_paths(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    foreign = tmp_path / "foreign.txt"
    foreign.write_text("peer\n", encoding="utf-8")
    _git(tmp_path, "add", "foreign.txt")
    _git(tmp_path, "commit", "-m", "seed")
    foreign.write_text("peer dirty\n", encoding="utf-8")
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    authored = tmp_path / "mine.py"
    authored.write_text("x=1\n", encoding="utf-8")
    residue = derive_tree_residue(
        source_repo=tmp_path,
        dispatch_id="unused",
        baseline=baseline,
    )
    assert residue.count == 1
    assert "mine.py" in residue.authored_paths
    assert "foreign.txt" not in residue.authored_paths


def test_inject_tree_residue_replaces_existing_line() -> None:
    body = "TYPE: CLOSEOUT\nstatus: complete\ntree_residue: 99\n"
    out = inject_tree_residue_line(body, count=3)
    assert "tree_residue: 3" in out
    assert "tree_residue: 99" not in out
