"""Lane-B S8 — hermetic AC10 four-clause isolation proof (§6)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

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
    capture_wt_baseline_with_hashes,
    prepare_closeout_delivery,
)
from services.git_integration_worker.cursor_sdk_closeout.delivery_assembly.orchestration import (
    _assemble_closeout_delivery,
)
from services.git_integration_worker.cursor_sdk_worktree import mint_dispatch_worktree


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _init_git_repo(path: Path) -> None:
    _git("init", "-b", "master", cwd=path)
    _git("config", "user.email", "test@example.com", cwd=path)
    _git("config", "user.name", "test", cwd=path)


def _test_cfg(tmp_path: Path, source_repo: Path, worktree_root: Path) -> WorkerConfig:
    dispatch_ws = tmp_path / "dispatch_ws"
    dispatch_ws.mkdir(parents=True, exist_ok=True)
    return WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace=dispatch_ws,
        green_gate_cmd=["true"],
    )


def _repo_manifest(dispatch_id: str, rel_path: str) -> EffectsManifest:
    return EffectsManifest(
        dispatch_id=dispatch_id,
        thread_id="thread-ac10-b",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[
                    EffectEntry(op="write", target=rel_path, identity=rel_path),
                ],
            )
        },
        coverage={"repo": "complete"},
    )


def _outcome(*, dispatch_id: str, rel_path: str) -> SdkRunOutcome:
    return SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
        effects_manifest=_repo_manifest(dispatch_id, rel_path),
        capture_branch="B",
    )


def _lane_b_closeout(
    *,
    cfg: WorkerConfig,
    binding: CaptureBinding,
    dispatch_id: str,
    baseline: dict[str, Any],
    files_expected: list[str],
) -> dict[str, Any]:
    delivery = _assemble_closeout_delivery(
        source_repo=cfg.source_repo,
        binding=binding,
        dispatch_id=dispatch_id,
        outcome=_outcome(dispatch_id=dispatch_id, rel_path=files_expected[0]),
        degraded_reason=None,
        thread_id="thread-ac10-b",
        work_item_ref=None,
        baseline=baseline,
        packet_text=(
            "<scope>\nFiles expected:\n"
            + "\n".join(f"- `{path}`" for path in files_expected)
            + "\n</scope>\n"
        ),
        files_expected=files_expected,
        cortex_artifact_paths=[],
        gate_d_created_rels=(),
        deliverables_expected=True,
        worktree_isolated=True,
    )
    return json.loads(delivery.body)


def _all_attributed_paths(payload: dict[str, Any]) -> set[str]:
    return {
        *payload.get("files_created", []),
        *payload.get("files_modified", []),
        *payload.get("files_deleted", []),
        *payload.get("files_outside_repo", []),
    }


def _assert_ac10_clause_3(lane_b_payload: dict[str, Any], own_path: str) -> None:
    assert own_path in lane_b_payload.get("files_modified", [])
    assert lane_b_payload.get("capture_status") == "complete"


def _assert_ac10_clauses(
    *,
    lane_b_payload: dict[str, Any],
    lane_a_payload: dict[str, Any],
    own_path: str,
    ambient_path: str,
) -> None:
    """Four-clause AC10 of architecture §6."""
    # Clause 1 — Lane-B excludes ambient repo movement.
    assert lane_b_payload.get("files_ambient_repo_movement") == []

    # Clause 2 — ambient path P is not attributed to Lane-B.
    assert ambient_path not in _all_attributed_paths(lane_b_payload)

    # Clause 3 — own write Q is attributed with complete capture.
    _assert_ac10_clause_3(lane_b_payload, own_path)

    # Clause 4 — control arm: Lane-A observes the ambient edit.
    lane_a_paths = _all_attributed_paths(lane_a_payload)
    lane_a_ambient = lane_a_payload.get("files_ambient_repo_movement") or []
    lane_a_ambient_paths = {entry["path"] for entry in lane_a_ambient}
    assert ambient_path in lane_a_paths or ambient_path in lane_a_ambient_paths


def _ac10_fixture(tmp_path: Path) -> dict[str, Any]:
    """Shared setup: Lane-B worktree edit Q + concurrent ambient edit P on master."""
    source_repo = tmp_path / "source_repo"
    source_repo.mkdir()
    worktree_root = tmp_path / "worktree_root"
    worktree_root.mkdir()
    _init_git_repo(source_repo)

    own_rel = "services/lane_b_q.py"
    ambient_rel = "parallel/ambient_lane_a.py"
    (source_repo / own_rel).parent.mkdir(parents=True, exist_ok=True)
    (source_repo / own_rel).write_text("v1\n", encoding="utf-8")
    _git("add", own_rel, cwd=source_repo)
    _git("commit", "-m", "seed q", cwd=source_repo)

    dispatch_id = "ac10-lane-b"
    write_tree = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    cfg = _test_cfg(tmp_path, source_repo, worktree_root)
    binding = CaptureBinding.lane_b(cfg, write_tree)

    baseline_b = capture_wt_baseline_with_hashes(
        write_tree,
        mount_root=binding.mount_root,
        repo_roots=binding.repo_roots,
    )
    assert baseline_b is not None

    baseline_a = capture_wt_baseline_with_hashes(
        source_repo,
        mount_root=CaptureBinding.lane_a(cfg).mount_root,
        repo_roots=CaptureBinding.lane_a(cfg).repo_roots,
    )
    assert baseline_a is not None

    (write_tree / own_rel).write_text("v2 lane-b\n", encoding="utf-8")
    ambient_file = source_repo / ambient_rel
    ambient_file.parent.mkdir(parents=True, exist_ok=True)
    ambient_file.write_text("# ambient parallel edit on shared master\n", encoding="utf-8")

    return {
        "cfg": cfg,
        "binding": binding,
        "dispatch_id": dispatch_id,
        "baseline_b": baseline_b,
        "baseline_a": baseline_a,
        "own_rel": own_rel,
        "ambient_rel": ambient_rel,
    }


def test_ac_s8_1_four_clause_ac10_isolation(tmp_path: Path) -> None:
    """AC-S8.1: AC10 four-clause conjunction holds for Lane-B vs Lane-A control."""
    fx = _ac10_fixture(tmp_path)
    lane_b_payload = _lane_b_closeout(
        cfg=fx["cfg"],
        binding=fx["binding"],
        dispatch_id=fx["dispatch_id"],
        baseline=fx["baseline_b"],
        files_expected=[fx["own_rel"]],
    )
    lane_a_delivery = prepare_closeout_delivery(
        source_repo=fx["cfg"].source_repo,
        binding=CaptureBinding.lane_a(fx["cfg"]),
        dispatch_id="ac10-lane-a-control",
        outcome=SdkRunOutcome(
            body="control",
            status="finished",
            duration_ms=10,
            tool_call_count=0,
        ),
        degraded_reason=None,
        thread_id="thread-ac10-a",
        work_item_ref=None,
        baseline=fx["baseline_a"],
    )
    lane_a_payload = json.loads(lane_a_delivery.body)

    _assert_ac10_clauses(
        lane_b_payload=lane_b_payload,
        lane_a_payload=lane_a_payload,
        own_path=fx["own_rel"],
        ambient_path=fx["ambient_rel"],
    )


def test_ac_s8_2_mutation_pre_s0_binding_fails_clause_3(tmp_path: Path) -> None:
    """AC-S8.2: broken pre-S0 binding (write_tree=source_repo) fails clause 3."""
    fx = _ac10_fixture(tmp_path)
    broken = CaptureBinding(
        lane="B",
        write_tree=fx["cfg"].source_repo.resolve(),
        receipt_tree=fx["cfg"].source_repo.resolve(),
        mount_root=fx["cfg"].source_repo.resolve(),
        repo_roots=(fx["cfg"].source_repo.resolve(),),
    )
    lane_b_payload = _lane_b_closeout(
        cfg=fx["cfg"],
        binding=broken,
        dispatch_id=fx["dispatch_id"],
        baseline=fx["baseline_b"],
        files_expected=[fx["own_rel"]],
    )

    with pytest.raises(AssertionError):
        _assert_ac10_clause_3(lane_b_payload, fx["own_rel"])
