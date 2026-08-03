"""Lane-B S5 — MCP workspaces:// write fence tests."""

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
from services.git_integration_worker.cursor_sdk_capture_divergence import (
    lane_b_workspaces_write_violation,
)
from services.git_integration_worker.cursor_sdk_capture_policy import (
    DeviationDisposition,
    disposition_for_deviation,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    capture_wt_baseline_with_hashes,
    prepare_closeout_delivery,
)
from services.git_integration_worker.cursor_sdk_packet import resolve_prompt_preamble


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


def _fs_manifest(
    *,
    op: str = "write",
    sandbox: str | None = None,
    path: str,
) -> EffectsManifest:
    detail: dict[str, str] = {"op": op, "path": path}
    if sandbox is not None:
        detail["sandbox"] = sandbox
    identity = path if sandbox is None else f"{sandbox}:{path}"
    return EffectsManifest(
        dispatch_id="disp-s5",
        thread_id="thread-s5",
        surfaces={
            "fs": SurfaceSection(
                surface="fs",
                source="test",
                entries=[
                    EffectEntry(
                        op="fs",
                        target=identity,
                        identity=identity,
                        detail=detail,
                    )
                ],
            )
        },
    )


def test_ac_s5_1_lane_b_workspaces_fs_write_hard_fails(tmp_path: Path) -> None:
    """AC-S5.1: Lane-B fs write to workspaces:// is a hard divergence."""
    cfg = _test_cfg(tmp_path)
    write_tree = cfg.worktree_root / "cursor-sdk-s5"
    write_tree.mkdir()
    _init_git_repo(write_tree)

    workspaces_uri = "workspaces://universal-llm-gateway/services/foo.py"
    manifest = _fs_manifest(path=workspaces_uri)
    binding = CaptureBinding.lane_b(cfg, write_tree)
    baseline = capture_wt_baseline_with_hashes(
        write_tree,
        mount_root=binding.mount_root,
        repo_roots=binding.repo_roots,
    )
    assert baseline is not None

    delivery = prepare_closeout_delivery(
        source_repo=cfg.source_repo,
        binding=binding,
        dispatch_id="disp-s5-ws",
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=50,
            tool_call_count=1,
            effects_manifest=manifest,
            capture_branch="B",
        ),
        degraded_reason=None,
        thread_id="thread-s5-ws",
        work_item_ref=None,
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    assert payload["capture_status"] != "complete"
    assert any(
        str(dev) == f"divergence:lane_b_workspaces_write:{workspaces_uri}"
        for dev in payload.get("deviations") or []
    )
    assert (
        disposition_for_deviation(f"divergence:lane_b_workspaces_write:{workspaces_uri}")
        == DeviationDisposition.HARD_FAIL
    )


def test_ac_s5_2_lane_b_cortex_fs_write_not_flagged(tmp_path: Path) -> None:
    """AC-S5.2: Lane-B cortex:// fs writes stay on the deliverable channel."""
    cfg = _test_cfg(tmp_path)
    write_tree = cfg.worktree_root / "cursor-sdk-s5-cortex"
    write_tree.mkdir()
    _init_git_repo(write_tree)

    cortex_uri = "cortex://notes/system/specs/lane-b-deliverable.md"
    manifest = _fs_manifest(sandbox="cortex", path="notes/system/specs/lane-b-deliverable.md")
    binding = CaptureBinding.lane_b(cfg, write_tree)
    baseline = capture_wt_baseline_with_hashes(
        write_tree,
        mount_root=binding.mount_root,
        repo_roots=binding.repo_roots,
    )
    assert baseline is not None

    delivery = prepare_closeout_delivery(
        source_repo=cfg.source_repo,
        binding=binding,
        dispatch_id="disp-s5-cortex",
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=50,
            tool_call_count=1,
            effects_manifest=manifest,
            capture_branch="B",
        ),
        degraded_reason=None,
        thread_id="thread-s5-cortex",
        work_item_ref=None,
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    assert not any(
        str(dev).startswith("divergence:lane_b_workspaces_write:")
        for dev in payload.get("deviations") or []
    )
    assert lane_b_workspaces_write_violation(manifest, "B") is None


def test_ac_s5_3_lane_a_workspaces_fs_write_not_flagged(tmp_path: Path) -> None:
    """AC-S5.3: Lane-A workspaces:// fs writes are not Lane-B fence violations."""
    cfg = _test_cfg(tmp_path)
    _init_git_repo(cfg.source_repo)
    workspaces_uri = "workspaces://universal-llm-gateway/services/foo.py"
    manifest = _fs_manifest(path=workspaces_uri)
    binding = CaptureBinding.lane_a(cfg)
    baseline = capture_wt_baseline_with_hashes(
        cfg.source_repo,
        mount_root=binding.mount_root,
        repo_roots=binding.repo_roots,
    )
    assert baseline is not None

    delivery = prepare_closeout_delivery(
        source_repo=cfg.source_repo,
        binding=binding,
        dispatch_id="disp-s5-lane-a",
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=50,
            tool_call_count=1,
            effects_manifest=manifest,
        ),
        degraded_reason=None,
        thread_id="thread-s5-lane-a",
        work_item_ref=None,
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    assert not any(
        str(dev).startswith("divergence:lane_b_workspaces_write:")
        for dev in payload.get("deviations") or []
    )
    assert lane_b_workspaces_write_violation(manifest, "A") is None


def test_ac_s5_4_lane_b_packet_preamble_native_tools_instruction() -> None:
    """AC-S5.4: Lane-B prompt preamble instructs native file tools for repo edits."""
    preamble = resolve_prompt_preamble(
        handoff_contract="implement",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
    )
    assert "LANE-B REPO EDITS" in preamble
    assert "native file tools" in preamble.lower()
    assert "workspaces://" in preamble
    assert "cortex://" in preamble

    lane_a = resolve_prompt_preamble(
        handoff_contract="implement",
        prompt_preamble=None,
        inferred_contract=None,
        lane=None,
    )
    assert "LANE-B REPO EDITS" not in lane_a
