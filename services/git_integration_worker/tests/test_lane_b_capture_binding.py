"""Lane-B S0 — CaptureBinding seam tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_dispatch_ledger import PromotedDispatch
from services.git_integration_worker.cursor_sdk_capture_binding import (
    CaptureBinding,
    binding_for_dispatch,
)
from services.git_integration_worker.cursor_sdk_dispatch_context import SdkDispatchContext
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    capture_wt_baseline_with_hashes,
    prepare_closeout_delivery,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    registered_repo_roots,
    resolve_mount_root,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest


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
    return WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=repo,
        worktree_root=wt_root,
        dispatch_workspace=tmp_path / "dispatch_ws",
        green_gate_cmd=["true"],
    )


def _closeout_kwargs(repo: Path) -> dict[str, object]:
    _init_git_repo(repo)
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    return {
        "source_repo": repo,
        "dispatch_id": "disp-s0",
        "outcome": outcome,
        "degraded_reason": None,
        "thread_id": "thread-s0",
        "work_item_ref": None,
        "baseline": None,
    }


def test_ac_s0_1_lane_a_binding_fields(tmp_path: Path) -> None:
    cfg = _test_cfg(tmp_path)
    binding = CaptureBinding.lane_a(cfg)
    mount = resolve_mount_root(cfg.source_repo)
    assert binding.lane == "A"
    assert binding.write_tree == cfg.source_repo.resolve()
    assert binding.receipt_tree == cfg.source_repo.resolve()
    assert binding.mount_root == mount
    assert binding.repo_roots == tuple(registered_repo_roots(mount))


def test_ac_s0_2_lane_a_closeout_byte_identical_with_binding(tmp_path: Path) -> None:
    cfg = _test_cfg(tmp_path)
    kwargs = _closeout_kwargs(cfg.source_repo)
    without = prepare_closeout_delivery(**kwargs)
    with_binding = prepare_closeout_delivery(
        **kwargs,
        binding=CaptureBinding.lane_a(cfg),
    )
    assert without.body == with_binding.body


def test_ac_s0_3_write_tree_vs_receipt_tree_split(tmp_path: Path) -> None:
    cfg = _test_cfg(tmp_path)
    write_tree = tmp_path / "write_tree"
    receipt_tree = tmp_path / "receipt_tree"
    write_tree.mkdir()
    receipt_tree.mkdir()
    _init_git_repo(write_tree)
    tracked = write_tree / "tracked.py"
    tracked.write_text("v1\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(write_tree), "add", "tracked.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(write_tree), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    baseline = capture_wt_baseline_with_hashes(write_tree)
    assert baseline is not None
    tracked.write_text("v2\n", encoding="utf-8")
    binding = CaptureBinding(
        lane="B",
        write_tree=write_tree.resolve(),
        receipt_tree=receipt_tree.resolve(),
        mount_root=write_tree.resolve(),
        repo_roots=(write_tree.resolve(),),
    )
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    delivery = prepare_closeout_delivery(
        source_repo=cfg.source_repo,
        binding=binding,
        dispatch_id="disp-split",
        outcome=outcome,
        degraded_reason=None,
        thread_id="thread-split",
        work_item_ref=None,
        baseline=baseline,
        deliverables_expected=True,
    )
    sidecar_rel = "tmp/reviews/closeouts/disp-split.md"
    assert (receipt_tree / sidecar_rel).is_file()
    assert not (write_tree / sidecar_rel).exists()
    payload = json.loads(delivery.body)
    assert "tracked.py" in payload["files_modified"]


def test_ac_s0_4_binding_for_lane_b_lease_key(tmp_path: Path) -> None:
    cfg = _test_cfg(tmp_path)
    wt = cfg.worktree_root / "cursor-sdk-promo"
    wt.mkdir()
    binding = binding_for_dispatch(cfg=cfg, lease_key=str(wt))
    assert binding.lane == "B"
    assert binding.write_tree == wt.resolve()
    assert binding.receipt_tree == cfg.source_repo.resolve()
    assert binding.write_tree != cfg.source_repo.resolve()


@pytest.mark.asyncio
async def test_ac_s0_4_promoted_dispatch_rebuilds_lane_b_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod
    from services.git_integration_worker.tests.test_cursor_sdk_route import (
        _make_controller,
    )

    cfg = _test_cfg(tmp_path)
    wt = cfg.worktree_root / "cursor-sdk-promo"
    wt.mkdir()
    monkeypatch.setattr(route_mod, "_CONFIG", cfg)

    captured: dict[str, object] = {}

    async def _capture_gated(**kwargs: object) -> None:
        captured["ctx"] = kwargs.get("ctx")

    monkeypatch.setattr(route_mod, "_run_sdk_dispatch_gated", _capture_gated)
    monkeypatch.setattr(route_mod, "_maybe_emit_giw_dispatched", lambda **_kw: None)
    monkeypatch.setattr(
        route_mod.CursorDispatchLedger.instance(),
        "load_promoted_request",
        lambda _promoted: CursorDispatchRequest(
            thread_id="thread-promo",
            model="cursor/composer-2.5",
            dispatch_id="promo-lane-b",
            execution_id="exec-promo",
            handoff_contract="implement",
            message="---\ncontract: implement\n---\np",
        ),
    )
    monkeypatch.setattr(
        route_mod.CursorDispatchLedger.instance(),
        "register_task",
        lambda *_args, **_kw: None,
    )
    monkeypatch.setattr(
        route_mod.CursorDispatchLedger.instance(),
        "mark_running",
        lambda *_args, **_kw: None,
    )

    promoted = PromotedDispatch(
        dispatch_id="promo-lane-b",
        thread_id="thread-promo",
        execution_id="exec-promo",
        caller_agent=None,
        resolved_model="composer-2.5",
        source_repo=str(cfg.source_repo.resolve()),
        contract="implement",
        read_only=False,
        record_json=json.dumps(
            {
                "model": "cursor/composer-2.5",
                "message": "---\ncontract: implement\n---\np",
                "handoff_contract": "implement",
            }
        ),
        lease_key=str(wt),
    )

    await route_mod._start_promoted_dispatch(
        promoted=promoted,
        controller=_make_controller(),
        request=None,
    )

    ctx = captured.get("ctx")
    assert isinstance(ctx, SdkDispatchContext)
    binding = ctx.capture_binding
    assert isinstance(binding, CaptureBinding)
    assert binding.write_tree == wt.resolve()
    assert binding.write_tree != cfg.source_repo.resolve()
    assert ctx.workspace_root == wt.resolve()
