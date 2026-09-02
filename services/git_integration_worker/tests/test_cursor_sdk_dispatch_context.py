"""Tests for SdkDispatchContext — per-dispatch tree and identity carrier."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding
from services.git_integration_worker.cursor_sdk_dispatch_context import (
    SdkDispatchContext,
)


def _test_cfg(tmp_path: Path) -> WorkerConfig:
    repo = tmp_path / "source_repo"
    repo.mkdir()
    wt_root = tmp_path / "worktree_root"
    wt_root.mkdir()
    return WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=repo,
        worktree_root=wt_root,
        dispatch_workspace=tmp_path / "dispatch_ws",
        green_gate_cmd=["true"],
    )


def test_workspace_root_lane_a_hub(tmp_path: Path) -> None:
    cfg = _test_cfg(tmp_path)
    hub = cfg.source_repo.resolve()
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    binding = CaptureBinding(
        lane="A",
        write_tree=hub,
        receipt_tree=hub,
        mount_root=hub,
        repo_roots=(hub,),
    )
    ctx = SdkDispatchContext(
        dispatch_id="disp-a",
        thread_id="thread-a",
        handoff_contract="consult",
        hub=cfg.source_repo,
        dispatch_workspace=projects_root,
        capture_binding=binding,
    )
    assert ctx.workspace_root == hub
    assert ctx.dispatch_workspace != ctx.workspace_root
    assert ctx.lane == "A"


def test_workspace_root_lane_a_satellite(tmp_path: Path) -> None:
    cfg = _test_cfg(tmp_path)
    satellite = tmp_path / "satellite"
    satellite.mkdir()
    binding = CaptureBinding.lane_a(cfg, dispatch_source_repo=satellite)
    ctx = SdkDispatchContext(
        dispatch_id="disp-sat",
        thread_id="thread-sat",
        handoff_contract="implement",
        hub=cfg.source_repo,
        dispatch_workspace=cfg.dispatch_workspace,
        capture_binding=binding,
    )
    assert ctx.workspace_root == satellite.resolve()
    assert ctx.hub == cfg.source_repo
    assert ctx.capture_binding.receipt_tree == cfg.source_repo.resolve()


def test_workspace_root_lane_b_worktree(tmp_path: Path) -> None:
    cfg = _test_cfg(tmp_path)
    worktree = tmp_path / "lane_b_wt"
    worktree.mkdir()
    binding = CaptureBinding.lane_b(cfg, worktree)
    ctx = SdkDispatchContext(
        dispatch_id="disp-b",
        thread_id="thread-b",
        handoff_contract="implement",
        hub=cfg.source_repo,
        dispatch_workspace=worktree,
        capture_binding=binding,
    )
    assert ctx.workspace_root == worktree.resolve()
    assert ctx.lane == "B"
    assert ctx.hub.resolve() == ctx.capture_binding.receipt_tree


def test_hub_receipt_tree_mismatch_rejected(tmp_path: Path) -> None:
    cfg = _test_cfg(tmp_path)
    hub = cfg.source_repo.resolve()
    other_receipt = tmp_path / "other_receipt"
    other_receipt.mkdir()
    binding = CaptureBinding(
        lane="A",
        write_tree=hub,
        receipt_tree=other_receipt,
        mount_root=hub,
        repo_roots=(hub,),
    )
    with pytest.raises(ValueError, match="hub=.*receipt_tree="):
        SdkDispatchContext(
            dispatch_id="disp-bad",
            thread_id="thread-bad",
            handoff_contract="consult",
            hub=cfg.source_repo,
            dispatch_workspace=hub,
            capture_binding=binding,
        )


def test_context_is_frozen(tmp_path: Path) -> None:
    cfg = _test_cfg(tmp_path)
    hub = cfg.source_repo.resolve()
    binding = CaptureBinding(
        lane="A",
        write_tree=hub,
        receipt_tree=hub,
        mount_root=hub,
        repo_roots=(hub,),
    )
    ctx = SdkDispatchContext(
        dispatch_id="disp-frozen",
        thread_id="thread-frozen",
        handoff_contract="consult",
        hub=cfg.source_repo,
        dispatch_workspace=hub,
        capture_binding=binding,
    )
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises((FrozenInstanceError, AttributeError)):
        ctx.hub = other  # type: ignore[misc]


def test_capture_binding_is_mandatory(tmp_path: Path) -> None:
    cfg = _test_cfg(tmp_path)
    with pytest.raises(TypeError):
        SdkDispatchContext(
            dispatch_id="disp-no-binding",
            thread_id="thread-no-binding",
            handoff_contract="consult",
            hub=cfg.source_repo,
            dispatch_workspace=cfg.source_repo,
        )  # type: ignore[call-arg]
