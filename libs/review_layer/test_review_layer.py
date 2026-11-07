"""Unit tests for review_layer detection, materialize, and GC."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from review_layer.detect import detect_review_targets, select_review_target
from review_layer.materialize import (
    ReviewTargetUnreachable,
    gc_expired_layers,
    layer_uri,
    materialize_review_layer,
)


def test_detect_lane_branch() -> None:
    body = "Branch cursor-sdk/lane-22520 carries the diff."
    result = detect_review_targets(body)
    assert result.layer_present is False
    assert len(result.targets) == 1
    assert result.targets[0].head == "cursor-sdk/lane-22520"
    assert result.targets[0].kind == "lane_branch"


def test_detect_worktree_path() -> None:
    body = (
        "Work in /mnt/torus/projects/ulg-arc-worktrees/"
        "universal-llm-gateway/lane-22520 only."
    )
    result = detect_review_targets(body)
    assert len(result.targets) == 1
    assert result.targets[0].head == "cursor-sdk/lane-22520"


def test_detect_review_head_and_base() -> None:
    body = "review_head: cursor-sdk/lane-99\nreview_base: master\n"
    result = detect_review_targets(body)
    assert len(result.targets) == 1
    t = result.targets[0]
    assert t.kind == "explicit_head"
    assert t.head == "cursor-sdk/lane-99"
    assert t.base == "master"


def test_bare_sha256_in_prose_no_target() -> None:
    sha = "a" * 64
    body = f"See commit {sha} in the narrative."
    result = detect_review_targets(body)
    assert result.targets == ()
    assert result.layer_present is False


def test_layer_present_marker() -> None:
    body = "Pre-built cortex://tmp/review-layers/foo/ cited."
    result = detect_review_targets(body)
    assert result.layer_present is True
    assert result.targets == ()


def _init_repo(repo: Path) -> tuple[str, str]:
    subprocess.check_call(["git", "init", "-b", "master"], cwd=repo)
    subprocess.check_call(
        ["git", "config", "user.email", "test@example.com"], cwd=repo
    )
    subprocess.check_call(["git", "config", "user.name", "test"], cwd=repo)
    (repo / "README.md").write_text("master\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "README.md"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "master"], cwd=repo)
    master_sha = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True)
        .strip()
    )
    subprocess.check_call(["git", "checkout", "-b", "feature"], cwd=repo)
    (repo / "delta.txt").write_text("branch\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "delta.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "feature"], cwd=repo)
    head_sha = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True)
        .strip()
    )
    subprocess.check_call(["git", "checkout", "master"], cwd=repo)
    return master_sha, head_sha


def test_materialize_unmerged_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cortex = tmp_path / "cortex"
    cortex.mkdir()
    _master_sha, head_sha = _init_repo(repo)

    manifest = materialize_review_layer(
        layer_id="layer-1",
        head_ref="feature",
        repo=repo,
        cortex_root=cortex,
    )
    assert manifest is not None
    layer_dir = cortex / "tmp/review-layers/layer-1"
    assert layer_dir.is_dir()
    assert not (cortex / "tmp/review-layers/layer-1.partial").exists()
    diff_bytes = (layer_dir / "diff.patch").read_bytes()
    assert manifest.diff_sha256 == hashlib.sha256(diff_bytes).hexdigest()
    saved = json.loads((layer_dir / "manifest.json").read_text(encoding="utf-8"))
    assert saved["head_sha"] == head_sha
    assert (layer_dir / "files/delta.txt").read_text(encoding="utf-8") == "branch"


def test_materialize_merged_branch_returns_none(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cortex = tmp_path / "cortex"
    _init_repo(repo)
    subprocess.check_call(["git", "merge", "feature", "-m", "merge"], cwd=repo)
    out = materialize_review_layer(
        layer_id="layer-2",
        head_ref="feature",
        repo=repo,
        cortex_root=cortex,
    )
    assert out is None
    assert not (cortex / "tmp/review-layers/layer-2").exists()


def test_materialize_bogus_ref(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    with pytest.raises(ReviewTargetUnreachable):
        materialize_review_layer(
            layer_id="layer-3",
            head_ref="no-such-branch",
            repo=repo,
            cortex_root=tmp_path / "cortex",
        )


def test_gc_expired_layers(tmp_path: Path) -> None:
    root = tmp_path / "layers"
    root.mkdir()
    now = datetime(2025, 1, 2, tzinfo=UTC)
    fresh = root / "fresh"
    fresh.mkdir()
    expired = root / "stale"
    expired.mkdir()
    manifest = {
        "expires_at": (now - timedelta(hours=1)).isoformat(),
    }
    (expired / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    fresh_manifest = {
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    }
    (fresh / "manifest.json").write_text(
        json.dumps(fresh_manifest), encoding="utf-8"
    )
    old_partial = root / "old.partial"
    old_partial.mkdir()
    old_ts = (now - timedelta(hours=2)).timestamp()
    import os

    os.utime(old_partial, (old_ts, old_ts))
    removed = gc_expired_layers(root, now=now)
    assert removed == 2
    assert not expired.exists()
    assert not old_partial.exists()
    assert fresh.exists()


def test_select_review_target_prefers_explicit() -> None:
    body = "cursor-sdk/lane-1\nreview_head: shaabc\n"
    result = detect_review_targets("review_head: deadbeef\n" + body)
    chosen = select_review_target(result)
    assert chosen is not None
    assert chosen.kind == "explicit_head"


def test_layer_uri() -> None:
    assert layer_uri("abc") == "cortex://tmp/review-layers/abc/"
