"""Hermetic tests for cursor-sdk ``workspace=`` satellite git identity."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from implement_admission.closeout_models import EffectEntry, EffectsManifest, SurfaceSection

from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding
from services.git_integration_worker.cursor_sdk_capture_status import (
    canonicalize_capture_path,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    capture_wt_baseline_with_hashes,
)
from services.git_integration_worker.cursor_sdk_closeout.delivery_assembly.orchestration import (
    _assemble_closeout_delivery,
)
from services.git_integration_worker.cursor_sdk_satellite_workspace import (
    CursorWorkspaceHubUseOmit,
    CursorWorkspaceNotGit,
    CursorWorkspaceUnknown,
    resolve_dispatch_source_repo,
)
from services.git_integration_worker.cursor_sdk_worktree import (
    mint_dispatch_worktree,
    resolve_admit_binding,
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
    marker = path / "README.md"
    marker.write_text("init\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(path), "add", "README.md"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def projects_layout(tmp_path: Path) -> tuple[Path, Path, Path, frozenset[str]]:
    """Hub + one allowlisted satellite under a shared projects root."""
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    hub = projects_root / "hub-gateway"
    hub.mkdir()
    _init_git_repo(hub)
    satellite = projects_root / "sat-bot"
    satellite.mkdir()
    _init_git_repo(satellite)
    roster = frozenset({"sat-bot"})
    roster_file = hub / "cursor-plugins/ulg-ecosystem/SATELLITES.txt"
    roster_file.parent.mkdir(parents=True)
    roster_file.write_text("sat-bot\n", encoding="utf-8")
    return hub, satellite, projects_root, roster


def test_omit_workspace_resolves_hub(projects_layout: tuple[Path, Path, Path, frozenset[str]]) -> None:
    hub, _sat, projects_root, roster = projects_layout
    assert resolve_dispatch_source_repo(
        None, hub=hub, projects_root=projects_root, allowlist=roster
    ) == hub.resolve()


def test_allowlisted_name_resolves_satellite(
    projects_layout: tuple[Path, Path, Path, frozenset[str]],
) -> None:
    hub, satellite, projects_root, roster = projects_layout
    resolved = resolve_dispatch_source_repo(
        "sat-bot", hub=hub, projects_root=projects_root, allowlist=roster
    )
    assert resolved == satellite.resolve()


def test_unknown_workspace_raises(projects_layout: tuple[Path, Path, Path, frozenset[str]]) -> None:
    hub, _sat, projects_root, roster = projects_layout
    with pytest.raises(CursorWorkspaceUnknown):
        resolve_dispatch_source_repo(
            "missing", hub=hub, projects_root=projects_root, allowlist=roster
        )


def test_hub_name_raises_use_omit(
    projects_layout: tuple[Path, Path, Path, frozenset[str]],
) -> None:
    hub, _sat, projects_root, roster = projects_layout
    with pytest.raises(CursorWorkspaceHubUseOmit):
        resolve_dispatch_source_repo(
            "universal-llm-gateway",
            hub=hub,
            projects_root=projects_root,
            allowlist=roster,
        )


def test_non_git_satellite_raises(projects_layout: tuple[Path, Path, Path, frozenset[str]]) -> None:
    hub, _sat, projects_root, _roster = projects_layout
    bare = projects_root / "bare-sat"
    bare.mkdir()
    roster = frozenset({"bare-sat"})
    with pytest.raises(CursorWorkspaceNotGit):
        resolve_dispatch_source_repo(
            "bare-sat", hub=hub, projects_root=projects_root, allowlist=roster
        )


def test_lane_a_satellite_binding_receipt_stays_hub(
    projects_layout: tuple[Path, Path, Path, frozenset[str]],
) -> None:
    hub, satellite, projects_root, _roster = projects_layout
    cfg = WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=hub,
        worktree_root=projects_root / "worktrees",
        dispatch_workspace=projects_root,
        green_gate_cmd=["true"],
    )
    binding = CaptureBinding.lane_a(cfg, dispatch_source_repo=satellite)
    assert binding.write_tree == satellite.resolve()
    assert binding.receipt_tree == hub.resolve()
    assert binding.repo_roots == (satellite.resolve(),)


def test_lane_b_mint_uses_satellite_git(
    projects_layout: tuple[Path, Path, Path, frozenset[str]],
) -> None:
    hub, satellite, projects_root, _roster = projects_layout
    wt_root = projects_root / "worktrees"
    wt_root.mkdir()
    wt = mint_dispatch_worktree(
        source_repo=satellite,
        worktree_root=wt_root,
        dispatch_id="sat-lane-b",
        thread_id="thread-sat",
    )
    toplevel = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    hub_top = subprocess.run(
        ["git", "-C", str(hub), "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert toplevel != hub_top
    assert Path(toplevel).resolve() == wt.resolve()


def test_lane_a_satellite_cwd_and_lease(
    projects_layout: tuple[Path, Path, Path, frozenset[str]],
) -> None:
    hub, satellite, projects_root, _roster = projects_layout
    req = CursorDispatchRequest(
        thread_id="t-sat-a",
        model="cursor/composer-2.5",
        dispatch_id="disp-sat-a",
        execution_id="exec-sat-a",
        message="---\ncontract: implement\n---\np",
        lane="A",
        workspace="sat-bot",
    )
    workspace, lease_key = resolve_admit_binding(
        req=req,
        source_repo=satellite,
        hub=hub,
        worktree_root=projects_root / "worktrees",
        dispatch_workspace_default=projects_root,
        lane="A",
    )
    assert workspace == satellite.resolve()
    assert lease_key == str(satellite.resolve())


def test_canonicalize_satellite_absolute_path(
    projects_layout: tuple[Path, Path, Path, frozenset[str]],
) -> None:
    _hub, satellite, projects_root, _roster = projects_layout
    raw = str(projects_root / "sat-bot" / "sentinel.py")
    result = canonicalize_capture_path(raw, source_repo=satellite)
    assert result.canonical_path == "sentinel.py"
    assert result.scope != "external_or_unknown"


def test_9575_class_sentinel_in_files_modified_not_outside(
    projects_layout: tuple[Path, Path, Path, frozenset[str]],
) -> None:
    hub, satellite, projects_root, _roster = projects_layout
    sentinel = satellite / "perps" / "sentinel.py"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("v1\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(satellite), "add", "perps/sentinel.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(satellite), "commit", "-m", "add sentinel"],
        check=True,
        capture_output=True,
    )
    cfg = WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=hub,
        worktree_root=projects_root / "worktrees",
        dispatch_workspace=projects_root,
        green_gate_cmd=["true"],
    )
    binding = CaptureBinding.lane_a(cfg, dispatch_source_repo=satellite)
    baseline = capture_wt_baseline_with_hashes(
        satellite,
        mount_root=binding.mount_root,
        repo_roots=binding.repo_roots,
    )
    assert baseline is not None
    sentinel.write_text("v2\n", encoding="utf-8")
    manifest = EffectsManifest(
        dispatch_id="9575-class",
        thread_id="thread-9575",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[
                    EffectEntry(
                        op="write",
                        target=str(sentinel.resolve()),
                        identity="perps/sentinel.py",
                    ),
                ],
            )
        },
    )
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=1,
        effects_manifest=manifest,
    )
    delivery = _assemble_closeout_delivery(
        source_repo=hub,
        binding=binding,
        dispatch_id="9575-class",
        outcome=outcome,
        degraded_reason=None,
        thread_id="thread-9575",
        work_item_ref=None,
        baseline=baseline,
        packet_text=(
            "---\nfiles_expected:\n- perps/sentinel.py\n---\n"
            "contract: implement\n"
        ),
        files_expected=["perps/sentinel.py"],
        cortex_artifact_paths=[],
        gate_d_created_rels=(),
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert "perps/sentinel.py" in payload["files_modified"]
    outside = payload.get("files_outside_repo") or []
    assert "perps/sentinel.py" not in outside
    assert not any(
        "sat-bot" in str(p) or str(sentinel.resolve()) in str(p) for p in outside
    )
    deviations = payload.get("deviations") or payload.get("baseline_deviations") or []
    assert "capture:outside_repo_paths_present" not in deviations
    assert "gate_d:no_expected_files_touched" not in deviations
    assert payload["head_sha"] != subprocess.run(
        ["git", "-C", str(hub), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_admit_rejects_unknown_workspace_via_resolver(
    projects_layout: tuple[Path, Path, Path, frozenset[str]],
) -> None:
    hub, _sat, projects_root, roster = projects_layout
    with pytest.raises(CursorWorkspaceUnknown):
        resolve_dispatch_source_repo(
            "not-in-roster",
            hub=hub,
            projects_root=projects_root,
            allowlist=roster,
        )
