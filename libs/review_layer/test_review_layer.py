"""Unit tests for review_layer detection, materialize, and GC."""

from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from review_layer.detect import (
    cited_layer_ids,
    detect_review_targets,
    implicit_target_heads,
    resolve_primary_target,
)
from review_layer.materialize import (
    ReviewTargetUnreachable,
    any_cited_layer_present,
    gc_expired_layers,
    materialize_review_layer,
    review_layer_exists,
    validate_layer_id,
)


def test_detect_lane_branch() -> None:
    body = "Branch cursor-sdk/lane-22520 carries the diff."
    result = detect_review_targets(body)
    assert len(result.targets) == 1
    assert result.targets[0].head == "cursor-sdk/lane-22520"


def test_lane_suffix_not_matched() -> None:
    body = "cursor-sdk/lane-22520-r2 is not a lane token."
    result = detect_review_targets(body)
    assert result.targets == ()


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
    t = result.targets[0]
    assert t.kind == "explicit_head"
    assert t.head == "cursor-sdk/lane-99"
    assert t.base == "master"


def test_bare_sha256_in_prose_no_target() -> None:
    sha = "a" * 64
    body = f"See commit {sha} in the narrative."
    assert detect_review_targets(body).targets == ()


def test_ambiguous_implicit_targets() -> None:
    body = "cursor-sdk/lane-1 and cursor-sdk/lane-2"
    result = detect_review_targets(body)
    assert len(implicit_target_heads(result)) == 2
    assert resolve_primary_target(result) is None


def test_explicit_head_wins_over_implicit() -> None:
    body = "cursor-sdk/lane-1\nreview_head: feature-x\n"
    target = resolve_primary_target(detect_review_targets(body))
    assert target is not None
    assert target.kind == "explicit_head"
    assert target.head == "feature-x"


def test_cited_layer_ids() -> None:
    body = "See cortex://tmp/review-layers/req-1/ and cortex://tmp/review-layers/req-2/"
    assert cited_layer_ids(body) == ("req-1", "req-2")


def test_layer_present_requires_manifest(tmp_path: Path) -> None:
    cortex = tmp_path / "cortex"
    layer = cortex / "tmp/review-layers/foo"
    layer.mkdir(parents=True)
    body = "Pre-built cortex://tmp/review-layers/foo/ cited."
    assert not any_cited_layer_present(body, cortex)
    (layer / "manifest.json").write_text("{}", encoding="utf-8")
    assert any_cited_layer_present(body, cortex)


def _init_repo(repo: Path) -> None:
    subprocess.check_call(["git", "init", "-b", "master"], cwd=repo)
    subprocess.check_call(
        ["git", "config", "user.email", "test@example.com"], cwd=repo
    )
    subprocess.check_call(["git", "config", "user.name", "test"], cwd=repo)
    (repo / "README.md").write_text("master\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "README.md"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "master"], cwd=repo)
    subprocess.check_call(["git", "checkout", "-b", "feature"], cwd=repo)
    (repo / "delta.txt").write_text("branch\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "delta.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "feature"], cwd=repo)
    subprocess.check_call(["git", "checkout", "master"], cwd=repo)


def test_materialize_unmerged_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cortex = tmp_path / "cortex"
    repo.mkdir()
    _init_repo(repo)
    manifest = materialize_review_layer(
        layer_id="layer-1",
        head_ref="feature",
        repo=repo,
        cortex_root=cortex,
    )
    assert manifest is not None
    layer_dir = cortex / "tmp/review-layers/layer-1"
    diff_bytes = (layer_dir / "diff.patch").read_bytes()
    merge_base = subprocess.check_output(
        ["git", "-C", str(repo), "merge-base", "master", "feature"],
        text=True,
    ).strip()
    git_diff = subprocess.check_output(
        ["git", "-C", str(repo), "diff", f"{merge_base}..feature"],
    )
    assert diff_bytes == git_diff
    assert manifest.diff_sha256 == hashlib.sha256(diff_bytes).hexdigest()
    assert (layer_dir / "files/delta.txt").read_bytes() == b"branch\n"
    assert manifest.files == ("delta.txt",)


def test_materialize_binary_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cortex = tmp_path / "cortex"
    repo.mkdir()
    _init_repo(repo)
    subprocess.check_call(["git", "checkout", "feature"], cwd=repo)
    (repo / "bin.dat").write_bytes(b"\x00\xff\xfe")
    subprocess.check_call(["git", "add", "bin.dat"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "bin"], cwd=repo)
    subprocess.check_call(["git", "checkout", "master"], cwd=repo)
    manifest = materialize_review_layer(
        layer_id="layer-bin",
        head_ref="feature",
        repo=repo,
        cortex_root=cortex,
    )
    assert manifest is not None
    blob = (cortex / "tmp/review-layers/layer-bin/files/bin.dat").read_bytes()
    assert blob == b"\x00\xff\xfe"


def test_materialize_idempotent_reuse(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cortex = tmp_path / "cortex"
    repo.mkdir()
    _init_repo(repo)
    first = materialize_review_layer(
        layer_id="layer-idem",
        head_ref="feature",
        repo=repo,
        cortex_root=cortex,
    )
    second = materialize_review_layer(
        layer_id="layer-idem",
        head_ref="feature",
        repo=repo,
        cortex_root=cortex,
    )
    assert first is not None and second is not None
    assert first.head_sha == second.head_sha


def test_materialize_merged_branch_returns_none(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cortex = tmp_path / "cortex"
    repo.mkdir()
    _init_repo(repo)
    subprocess.check_call(["git", "merge", "feature", "-m", "merge"], cwd=repo)
    assert (
        materialize_review_layer(
            layer_id="layer-2",
            head_ref="feature",
            repo=repo,
            cortex_root=cortex,
        )
        is None
    )


def test_explicit_bogus_ref(tmp_path: Path) -> None:
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


def test_review_base_uses_merge_base(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cortex = tmp_path / "cortex"
    repo.mkdir()
    _init_repo(repo)
    subprocess.check_call(["git", "checkout", "master"], cwd=repo)
    (repo / "master-only.txt").write_text("m\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "master-only.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "master advance"], cwd=repo)
    manifest = materialize_review_layer(
        layer_id="layer-base",
        head_ref="feature",
        base_ref="master",
        repo=repo,
        cortex_root=cortex,
    )
    assert manifest is not None
    merge_base = subprocess.check_output(
        ["git", "-C", str(repo), "merge-base", "master", "feature"],
        text=True,
    ).strip()
    assert manifest.base_sha == merge_base


def test_validate_layer_id_rejects_partial_suffix() -> None:
    with pytest.raises(ReviewTargetUnreachable):
        validate_layer_id("foo.partial")


def test_gc_expired_and_corrupt_manifest(tmp_path: Path) -> None:
    root = tmp_path / "layers"
    root.mkdir()
    now = datetime(2025, 1, 2, tzinfo=UTC)
    stale = root / "stale"
    stale.mkdir()
    (stale / "manifest.json").write_text("not-json", encoding="utf-8")
    import os

    old_ts = (now - timedelta(hours=30)).timestamp()
    os.utime(stale, (old_ts, old_ts))
    removed = gc_expired_layers(root, now=now)
    assert removed >= 1
    assert not stale.exists()


def test_review_layer_exists(tmp_path: Path) -> None:
    cortex = tmp_path / "c"
    path = cortex / "tmp/review-layers/x/manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    assert review_layer_exists(cortex, "x")
    assert not review_layer_exists(cortex, "y")
