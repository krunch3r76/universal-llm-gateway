"""Lane-B S1 — outside-repo anchor + census short-circuit tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
)
from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    _assemble_closeout_delivery,
    capture_wt_baseline_with_hashes,
    prepare_closeout_delivery,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    repo_change_set_from_manifest,
    snapshot_outside_repo_paths,
)


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    for key, value in (("user.email", "test@example.com"), ("user.name", "test")):
        subprocess.run(
            ["git", "-C", str(path), "config", key, value],
            check=True,
            capture_output=True,
        )


def _test_cfg(tmp_path: Path) -> WorkerConfig:
    repo = tmp_path / "source_repo"
    repo.mkdir()
    wt_root = tmp_path / "worktree_root"
    wt_root.mkdir()
    dispatch_ws = tmp_path / "dispatch_ws"
    dispatch_ws.mkdir()
    return WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=repo,
        worktree_root=wt_root,
        dispatch_workspace=dispatch_ws,
        green_gate_cmd=["true"],
    )


def test_ac_s1_1_lane_b_snapshot_short_circuit_without_rglob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-S1.1: single-tree Lane-B binding skips outside-repo census walk."""
    cfg = _test_cfg(tmp_path)
    write_tree = cfg.worktree_root / "cursor-sdk-test"
    write_tree.mkdir()
    _init_git_repo(write_tree)
    outside_marker = write_tree / "would-be-outside.txt"
    outside_marker.write_text("noise\n", encoding="utf-8")

    def _rglob_must_not_run(self: Path, pattern: str) -> object:
        raise AssertionError(f"Path.rglob must not run for Lane-B census: {pattern!r}")

    monkeypatch.setattr(Path, "rglob", _rglob_must_not_run)

    binding = CaptureBinding.lane_b(cfg, write_tree)
    outside = snapshot_outside_repo_paths(
        binding.mount_root,
        list(binding.repo_roots),
    )
    assert outside == frozenset()


def test_ac_s1_1_baseline_uses_binding_outside_repo_census(tmp_path: Path) -> None:
    """AC-S1.1: admit baseline outside_repo is empty for Lane-B binding."""
    cfg = _test_cfg(tmp_path)
    write_tree = cfg.worktree_root / "cursor-sdk-test"
    write_tree.mkdir()
    _init_git_repo(write_tree)
    sibling = cfg.dispatch_workspace / "parallel-outside.md"
    sibling.write_text("outside worktree\n", encoding="utf-8")

    binding = CaptureBinding.lane_b(cfg, write_tree)
    baseline = capture_wt_baseline_with_hashes(
        write_tree,
        mount_root=binding.mount_root,
        repo_roots=binding.repo_roots,
    )
    assert baseline is not None
    assert baseline["outside_repo"] == []


def test_ac_s1_2_lane_b_dispatch_workspace_write_hard_fails(tmp_path: Path) -> None:
    """AC-S1.2: outside-worktree write yields unknown_root_child hard-fail."""
    cfg = _test_cfg(tmp_path)
    write_tree = cfg.worktree_root / "cursor-sdk-disp"
    write_tree.mkdir()
    _init_git_repo(write_tree)
    tracked = write_tree / "services" / "x.py"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("# x\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(write_tree), "add", "services/x.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(write_tree), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )

    outside_rel = "outside-dispatch.md"
    outside_path = cfg.dispatch_workspace / outside_rel
    outside_path.write_text("# outside worktree\n", encoding="utf-8")

    binding = CaptureBinding.lane_b(cfg, write_tree)
    baseline = capture_wt_baseline_with_hashes(
        write_tree,
        mount_root=binding.mount_root,
        repo_roots=binding.repo_roots,
    )
    assert baseline is not None

    manifest = EffectsManifest(
        dispatch_id="disp-outside",
        thread_id="thread-outside",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[
                    EffectEntry(
                        op="write",
                        target=str(outside_path.resolve()),
                        identity=str(outside_path.resolve()),
                    ),
                    EffectEntry(
                        op="write",
                        target="services/x.py",
                        identity="services/x.py",
                    ),
                ],
            )
        },
    )
    _, manifest_outside, _ = repo_change_set_from_manifest(
        manifest,
        source_repo=write_tree,
        mount_root=binding.mount_root,
        repo_roots=list(binding.repo_roots),
    )
    assert manifest_outside

    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
        effects_manifest=manifest,
        capture_branch="B",
    )
    delivery = _assemble_closeout_delivery(
        source_repo=cfg.source_repo,
        binding=binding,
        dispatch_id="disp-outside",
        outcome=outcome,
        degraded_reason=None,
        thread_id="thread-outside",
        work_item_ref=None,
        baseline=baseline,
        packet_text="<scope>\nFiles expected: - `services/x.py`\n</scope>\n",
        files_expected=["services/x.py"],
        cortex_artifact_paths=[],
        gate_d_created_rels=(),
        deliverables_expected=True,
        worktree_isolated=True,
    )
    payload = json.loads(delivery.body)
    outside_paths = payload.get("files_outside_repo") or []
    assert outside_paths, "expected non-empty outside_repo_paths for isolation falsifier"
    assert any(
        str(dev).startswith("divergence:unknown_root_child:")
        for dev in payload.get("deviations") or []
    )
    assert payload["capture_status"] == "partial"


def test_ac_s1_3_lane_a_ignores_minted_lane_b_worktree_files(tmp_path: Path) -> None:
    """AC-S1.3 / F5: Lane-A census ignores parallel Lane-B worktree mint."""
    mount = tmp_path / "projects"
    mount.mkdir()
    source_repo = mount / "universal-llm-gateway"
    source_repo.mkdir()
    worktree_root = mount / "ulg-arc-worktrees"
    worktree_root.mkdir()
    _init_git_repo(source_repo)

    lane_b_tree = worktree_root / "cursor-sdk-parallel"
    lane_b_tree.mkdir()
    _init_git_repo(lane_b_tree)
    parallel_rel = "parallel/lane-b-only.py"
    parallel_file = lane_b_tree / parallel_rel
    parallel_file.parent.mkdir(parents=True)
    parallel_file.write_text("# lane b\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(lane_b_tree), "add", parallel_rel],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(lane_b_tree), "commit", "-m", "lane b init"],
        check=True,
        capture_output=True,
    )

    cfg = WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace=mount,
        green_gate_cmd=["true"],
    )
    binding = CaptureBinding.lane_a(cfg)
    baseline = capture_wt_baseline_with_hashes(
        source_repo,
        mount_root=binding.mount_root,
        repo_roots=binding.repo_roots,
    )
    assert baseline is not None

    wt_mount_rel = str(lane_b_tree.resolve().relative_to(mount.resolve()))
    assert not any(
        p == wt_mount_rel or p.startswith(f"{wt_mount_rel}/")
        for p in baseline["outside_repo"]
    )

    delivery = prepare_closeout_delivery(
        source_repo=source_repo,
        binding=binding,
        dispatch_id="disp-lane-a",
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=50,
            tool_call_count=0,
        ),
        degraded_reason=None,
        thread_id="thread-lane-a",
        work_item_ref=None,
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    files_outside = payload.get("files_outside_repo") or []
    assert not any(
        p == wt_mount_rel or p.startswith(f"{wt_mount_rel}/") for p in files_outside
    )
