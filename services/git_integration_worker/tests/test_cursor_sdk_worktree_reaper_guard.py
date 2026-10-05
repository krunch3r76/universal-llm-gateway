"""Live-bridge guard on Lane-B worktree removal (H4, todo:cursor-sdk-bridge-death-root-cause).

Lane-B dispatches were dying mid-run with ``Error: spawn /bin/bash ENOENT``. The
shell was never missing: a sweep had removed the worktree the bridge was
standing in, and Node reports a missing ``cwd`` by naming the executable it was
about to launch. ``test_deleted_cwd_reports_enoent_naming_the_shell`` pins that
signature so the diagnosis cannot be re-litigated from the message alone; the
rest assert that no remove path fires while a bridge holds the directory.
"""

from __future__ import annotations

import errno
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_orphan import BridgeOccupancy
from services.git_integration_worker.cursor_sdk_park_ledger import mark_parked
from services.git_integration_worker.cursor_sdk_worktree import (
    mint_dispatch_worktree,
    reap_orphan_worktrees,
)
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    _occupancy_snapshot,
    containing_worktree_under_root,
    live_bridge_worktree_paths,
    live_ledger_worktree_paths,
    reset_occupancy_cache,
    worktree_held_by_live_bridge,
)
from services.git_integration_worker.cursor_sdk_worktree_prune import (
    _GHOST_EMIT_BUDGET,
    active_managed_worktree_paths,
    prune_dispatch_worktree,
    reset_ghost_row_reports,
    rollback_dispatch_worktree,
)
from services.git_integration_worker.cursor_sdk_worktree_reconcile import (
    reconcile_unregistered_worktrees,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    lookup_lane_worktree,
    register_lane_worktree,
    unregister_lane_worktree,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    from services.git_integration_worker.cursor_sdk_shell_cwd import (
        reset_shell_spawn_state,
    )

    reset_ghost_row_reports()
    reset_occupancy_cache()
    reset_shell_spawn_state()
    yield
    CursorDispatchLedger._instance = None
    reset_ghost_row_reports()
    reset_occupancy_cache()
    reset_shell_spawn_state()


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "test", cwd=repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)
    return repo


def _stub_occupancy(
    monkeypatch: pytest.MonkeyPatch,
    *bridges: BridgeOccupancy,
) -> None:
    """Replace the psutil scan with a fixed bridge roster."""
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_orphan.live_bridge_occupancy",
        lambda *_a, **_k: list(bridges),
    )
    reset_occupancy_cache()


def _admit(
    *,
    ledger: CursorDispatchLedger,
    dispatch_id: str,
    thread_id: str,
    source_repo: Path,
    lease_key: str,
) -> None:
    req = CursorDispatchRequest(
        thread_id=thread_id,
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message="work",
        worktree_isolated=True,
    )
    ledger.admit(
        req=req,
        fingerprint=f"fp-{dispatch_id}",
        execution_id=f"exec-{dispatch_id}",
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            model_id="composer-2.5",
        ),
        source_repo=str(source_repo.resolve()),
        lease_key=lease_key,
        contract="implement",
        worker_instance="worker-a",
    )


def test_deleted_cwd_reports_enoent_naming_the_shell(tmp_path: Path) -> None:
    """The ENOENT signature comes from a missing cwd, not a missing ``/bin/bash``.

    Python attributes the failure to the directory it could not ``chdir`` into.
    Node attributes the identical failure to the spawn target, which is why the
    bridge stderr read as a missing shell and sent the first investigation into
    sandbox configuration.
    """
    assert Path("/bin/bash").exists()
    missing = tmp_path / "reaped-lane-tree"

    with pytest.raises(OSError) as exc_info:
        subprocess.run(
            ["/bin/bash", "-c", "pwd"],
            cwd=str(missing),
            check=False,
            capture_output=True,
        )
    assert exc_info.value.errno == errno.ENOENT
    assert exc_info.value.filename == str(missing)

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not on PATH — Python half of the signature still asserted")
    probe = (
        "const cp=require('child_process');"
        f"const p=cp.spawn('/bin/bash',['-c','pwd'],{{cwd:{json.dumps(str(missing))}}});"
        "p.on('error',e=>console.log(JSON.stringify("
        "{code:e.code,message:e.message,path:e.path})));"
    )
    proc = subprocess.run(
        [node, "-e", probe],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    reported = json.loads(proc.stdout.strip())
    assert reported["code"] == "ENOENT"
    assert reported["message"] == "spawn /bin/bash ENOENT"
    assert reported["path"] == "/bin/bash"


def test_containing_worktree_resolves_bridge_subdirectory(tmp_path: Path) -> None:
    """A bridge cwd deep inside a lane tree still pins the lane tree itself."""
    root = tmp_path / "worktrees"
    nested = root / "lane-10143" / "services" / "git_integration_worker"
    nested.mkdir(parents=True)

    assert containing_worktree_under_root(path=nested, worktree_root=root) == str(
        (root / "lane-10143").resolve()
    )
    assert containing_worktree_under_root(path=root, worktree_root=root) is None
    assert (
        containing_worktree_under_root(path=tmp_path / "elsewhere", worktree_root=root)
        is None
    )


@pytest.mark.offline
def test_containing_worktree_resolves_two_level_subroot(tmp_path: Path) -> None:
    """Per-repo subroot layout resolves the lane directory, not the repo slug."""
    root = tmp_path / "worktrees"
    nested = (
        root
        / "universal-llm-gateway"
        / "lane-10273"
        / "services"
        / "git_integration_worker"
    )
    nested.mkdir(parents=True)

    assert containing_worktree_under_root(path=nested, worktree_root=root) == str(
        (root / "universal-llm-gateway" / "lane-10273").resolve()
    )


def test_prune_refuses_worktree_held_by_live_bridge(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1: a live bridge cwd blocks ``prune_dispatch_worktree`` outright."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "guard-prune"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    _stub_occupancy(
        monkeypatch,
        BridgeOccupancy(pid=4242, cwd=str(wt), dispatch_id=dispatch_id),
    )

    result = prune_dispatch_worktree(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )

    assert not result.pruned
    assert result.branch_retained
    assert wt.is_dir()
    assert (
        lookup_lane_worktree(thread_id=dispatch_id, source_repo=source_repo) is not None
    )
    assert worktree_held_by_live_bridge(worktree_path=wt) == 4242


def test_ac_w0_4_fresh_rescan_detects_bridge_inside_ttl(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-W0-4: ``fresh=True`` sees a bridge that appeared after the cache filled."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "fresh-guard"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_orphan.live_bridge_occupancy",
        lambda *_a, **_k: [],
    )
    reset_occupancy_cache()
    _occupancy_snapshot()

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_orphan.live_bridge_occupancy",
        lambda *_a, **_k: [
            BridgeOccupancy(pid=5555, cwd=str(wt), dispatch_id=dispatch_id),
        ],
    )

    result = prune_dispatch_worktree(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )

    assert not result.pruned
    assert result.branch_retained
    assert wt.is_dir()


def test_ac_w0_5_rollback_emits_worktree_removed(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-W0-5: rollback removal emits ``sdk.lane_b.worktree_removed`` trigger=rollback."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "rollback-emit"
    thread_id = "t-rollback-emit"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    _stub_occupancy(monkeypatch)
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_release."
        "emit_sdk_lane_b_worktree_removed",
        lambda **kwargs: emitted.append(dict(kwargs)),
    )

    result = rollback_dispatch_worktree(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        source_repo=source_repo,
    )

    assert result.pruned
    assert not wt.exists()
    assert len(emitted) == 1
    assert emitted[0]["trigger"] == "rollback"
    assert emitted[0]["dispatch_id"] == dispatch_id


def test_ac_w0_6b_terminal_sibling_bridge_live_tree_survives(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-W0-6b: terminal sibling ledger row + live bridge → tree survives rollback."""
    worktree_root = tmp_path / "worktrees"
    thread_id = "t-10143-shape"
    dispatch_id = "victim-dispatch"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    ledger = CursorDispatchLedger.instance()
    sibling_id = "sibling-terminal"
    _admit(
        ledger=ledger,
        dispatch_id=sibling_id,
        thread_id=thread_id,
        source_repo=source_repo,
        lease_key=str(wt.resolve()),
    )
    ledger.mark_terminal(dispatch_id=sibling_id, terminal_status="completed")
    _stub_occupancy(
        monkeypatch,
        BridgeOccupancy(pid=7777, cwd=str(wt), dispatch_id=sibling_id),
    )

    result = rollback_dispatch_worktree(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        source_repo=source_repo,
    )

    assert not result.pruned
    assert wt.is_dir()


def test_prune_proceeds_when_no_bridge_holds_the_tree(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard is not a blanket refusal: an unoccupied tree still prunes."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "guard-prune-free"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    _stub_occupancy(
        monkeypatch,
        BridgeOccupancy(
            pid=99,
            cwd=str(tmp_path / "worktrees" / "lane-other"),
            dispatch_id="someone-else",
        ),
    )

    result = prune_dispatch_worktree(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )

    assert result.pruned
    assert not wt.exists()


def test_reconcile_leaves_unregistered_tree_held_by_shell_spawn_cwd(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shell cwd under a scratch tree holds it even when the node cwd is elsewhere.

    The node bridge stays at the lane root. The dying spawn's cwd is the
    in-flight shell ``workingDirectory`` (or a bash child standing there).
    """
    from services.git_integration_worker.cursor_sdk_shell_cwd import (
        note_shell_tool_call,
        reset_shell_spawn_state,
    )

    worktree_root = tmp_path / "worktrees"
    dispatch_id = "guard-shell-cwd"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    unregister_lane_worktree(thread_id=dispatch_id, source_repo=source_repo)
    reset_shell_spawn_state()
    note_shell_tool_call(
        dispatch_id,
        tool_name="shell",
        status="running",
        args={"command": "ls", "workingDirectory": str(wt)},
    )
    _stub_occupancy(
        monkeypatch,
        BridgeOccupancy(pid=778, cwd=str(tmp_path), dispatch_id=dispatch_id),
    )

    reconciled, surfaced = reconcile_unregistered_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )

    assert reconciled == 0
    assert surfaced == 0
    assert wt.is_dir()


def test_reconcile_holds_sibling_scratch_prefixed_by_live_lane(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live lane holds ``lane-{thread}-*`` before any shell enters it.

    13350 archived ``lane-13350-12458`` after ``git worktree add`` while the
    bridge still stood in the registered lane. Occupancy cwd is that lane;
    nothing records a shell spawn cwd.
    """
    worktree_root = tmp_path / "worktrees"
    thread_id = "sib-hold"
    dispatch_id = "guard-sib-hold"
    lane = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    sibling = lane.parent / f"{lane.name}-12458"
    _git(
        "worktree",
        "add",
        "-b",
        "cursor-sdk/sib-hold-12458",
        str(sibling),
        "HEAD",
        cwd=source_repo,
    )
    _stub_occupancy(
        monkeypatch,
        BridgeOccupancy(pid=881, cwd=str(lane), dispatch_id=dispatch_id),
    )

    reconciled, surfaced = reconcile_unregistered_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )

    assert (reconciled, surfaced) == (0, 0)
    assert sibling.is_dir()


def test_reconcile_does_not_hold_lane_suffix_without_hyphen(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``lane-{thread}0`` shares a string prefix but is not a sibling hold."""
    worktree_root = tmp_path / "worktrees"
    thread_id = "sib-bound"
    dispatch_id = "guard-sib-bound"
    lane = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    decoy = lane.parent / f"{lane.name}0"
    _git(
        "worktree",
        "add",
        "-b",
        "cursor-sdk/sib-bound-nohyphen",
        str(decoy),
        "HEAD",
        cwd=source_repo,
    )
    occupancy = (BridgeOccupancy(pid=882, cwd=str(lane), dispatch_id=dispatch_id),)
    _stub_occupancy(monkeypatch, *occupancy)
    held = live_bridge_worktree_paths(
        worktree_root=worktree_root,
        occupancy=list(occupancy),
    )
    assert str(decoy.resolve()) not in held

    reconciled, surfaced = reconcile_unregistered_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )

    assert (reconciled, surfaced) == (1, 0)
    assert not decoy.exists()


def test_live_bridge_paths_include_shell_child_cwd(tmp_path: Path) -> None:
    """A bash child inside a scratch tree pins that tree, not only the node cwd."""
    root = tmp_path / "worktrees"
    lane = root / "lane-root"
    scratch = root / "lane-scratch"
    nested = scratch / "services"
    lane.mkdir(parents=True)
    nested.mkdir(parents=True)
    held = live_bridge_worktree_paths(
        worktree_root=root,
        occupancy=[
            BridgeOccupancy(
                pid=1,
                cwd=str(lane),
                dispatch_id="d-child",
                shell_cwds=(str(nested),),
            )
        ],
    )
    assert str(scratch.resolve()) in held
    assert str(lane.resolve()) in held


def test_reconcile_leaves_unregistered_tree_held_by_live_bridge(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1/AC2: the lane-10143 path — registry lost the row, the bridge still runs.

    Without the guard this tree is archived and removed (the removal is
    asserted by ``test_falsifier_f_a4_prune_on_terminal_and_reaper_path``),
    which is exactly the deletion that kills a live dispatch's shell.
    """
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "guard-reconcile"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    unregister_lane_worktree(thread_id=dispatch_id, source_repo=source_repo)
    _stub_occupancy(
        monkeypatch,
        BridgeOccupancy(
            pid=777,
            cwd=str(wt / "services"),
            dispatch_id=dispatch_id,
        ),
    )

    reconciled, surfaced = reconcile_unregistered_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )

    assert reconciled == 0
    assert surfaced == 0
    assert wt.is_dir()


def test_sweep_leaves_tree_held_by_env_stamped_bridge(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bridge that chdir'd away is still pinned through its dispatch stamp."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "guard-sweep-env"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger=ledger,
        dispatch_id=dispatch_id,
        thread_id=dispatch_id,
        source_repo=source_repo,
        lease_key=str(wt.resolve()),
    )
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    unregister_lane_worktree(thread_id=dispatch_id, source_repo=source_repo)
    _stub_occupancy(
        monkeypatch,
        BridgeOccupancy(pid=1234, cwd="/", dispatch_id=dispatch_id),
    )

    held = live_bridge_worktree_paths(worktree_root=worktree_root)
    assert str(wt.resolve()) in held

    sweep = reap_orphan_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )

    assert sweep.worktrees_reconciled == 0
    assert sweep.live_bridge_holds >= 1
    assert wt.is_dir()


def test_active_set_covers_lane_row_when_lease_key_points_outside_root(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1: registry status lag — a running row whose lease key is the hub path.

    The pre-fix scan only accepted ``lease_key``/``source_repo`` values already
    under ``worktree_root``, so a running Lane-B dispatch registered against
    the hub checkout contributed nothing to the active set and its lane tree
    looked free.
    """
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "guard-active"
    thread_id = "10180"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger=ledger,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        source_repo=source_repo,
        lease_key=str(source_repo.resolve()),
    )
    ledger.mark_running(dispatch_id=dispatch_id)
    _stub_occupancy(monkeypatch)

    ledger_paths = live_ledger_worktree_paths(worktree_root=worktree_root)
    assert str(wt.resolve()) in ledger_paths
    assert str(wt.resolve()) in active_managed_worktree_paths(
        worktree_root=worktree_root
    )


def test_open_park_for_restart_holds_worktree_through_boot_reap(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """a:37507: cancelled + open park_for_restart is occupancy, not an orphan.

    mark_parked writes status=cancelled before resume admits a child. Boot
    ``reap_orphan_worktrees`` runs first and treats cancelled as reapable.
    Breaks when the live-ledger scan only lists admitted|running|queued|
    parked_waiting: the satellite (or hub) lane tree is gone when pin runs.
    No sdk_agent_id so resume_retain cannot be the reason the tree survives.
    """
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "guard-open-park"
    thread_id = "37507"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger=ledger,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        source_repo=source_repo,
        lease_key=str(wt.resolve()),
    )
    ledger.mark_running(dispatch_id=dispatch_id)
    mark_parked(
        dispatch_id=dispatch_id,
        intent_id="intent-37507",
        drain_epoch=1,
        actor="manage",
        reason="deploy",
        requested_at="2026-10-03T00:00:00Z",
        method="run_cancel",
        tool_call_count=0,
        last_tool_calls=[],
        sidecar_uri=None,
    )
    _stub_occupancy(monkeypatch)

    assert str(wt.resolve()) in live_ledger_worktree_paths(worktree_root=worktree_root)
    sweep = reap_orphan_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )
    assert sweep.reaped == 0
    assert wt.is_dir()


def test_open_park_occupancy_ignores_resume_of_without_park_admit(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """a:37672: a refused/hand resume_of row must not drop the occupancy hold."""
    from services.git_integration_worker.cursor_sdk_conductor_park_gate import (
        OPEN_RESTART_PARK_SQL,
        open_restart_park_sql,
    )
    from services.git_integration_worker.cursor_sdk_park_resume import (
        ADMITTED_VIA_PARK_RESUME,
    )

    assert OPEN_RESTART_PARK_SQL == open_restart_park_sql()
    assert "d.park_kind" in open_restart_park_sql("d")
    assert ADMITTED_VIA_PARK_RESUME in OPEN_RESTART_PARK_SQL

    worktree_root = tmp_path / "worktrees"
    dispatch_id = "guard-stray-child"
    thread_id = "37672"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger=ledger,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        source_repo=source_repo,
        lease_key=str(wt.resolve()),
    )
    ledger.mark_running(dispatch_id=dispatch_id)
    mark_parked(
        dispatch_id=dispatch_id,
        intent_id="intent-37672",
        drain_epoch=1,
        actor="manage",
        reason="deploy",
        requested_at="2026-10-03T00:00:00Z",
        method="run_cancel",
        tool_call_count=0,
        last_tool_calls=[],
        sidecar_uri=None,
    )
    stray = CursorDispatchRequest(
        thread_id=thread_id,
        model="cursor/composer-2.5",
        dispatch_id="stray-resume-child",
        execution_id="exec-stray",
        message="refused resume",
        resume_of=dispatch_id,
        worktree_isolated=True,
    )
    ledger.admit(
        req=stray,
        fingerprint="fp-stray",
        execution_id="exec-stray",
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id="stray-resume-child",
            thread_id=thread_id,
            model_id="composer-2.5",
        ),
        source_repo=str(source_repo.resolve()),
        lease_key=str(wt.resolve()),
        contract="implement",
        worker_instance="worker-a",
    )
    _stub_occupancy(monkeypatch)
    assert str(wt.resolve()) in live_ledger_worktree_paths(worktree_root=worktree_root)
    sweep = reap_orphan_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )
    assert sweep.reaped == 0
    assert wt.is_dir()


def test_registry_ghost_row_is_surfaced_not_dropped(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lane row whose directory vanished is reported; the row stays put.

    Dropping it would unpin the branch for merged-branch GC, and a tree that
    disappeared is precisely the case where the branch tip may hold the only
    copy of the work.
    """
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "guard-ghost"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger=ledger,
        dispatch_id=dispatch_id,
        thread_id=dispatch_id,
        source_repo=source_repo,
        lease_key=str(wt.resolve()),
    )
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    shutil.rmtree(wt)
    _stub_occupancy(monkeypatch)

    sweep = reap_orphan_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )

    assert sweep.registry_ghost_rows >= 1
    assert (
        lookup_lane_worktree(thread_id=dispatch_id, source_repo=source_repo) is not None
    )


def test_ghost_row_backlog_is_counted_in_full_but_emits_within_budget(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-existing backlog is counted exactly and announced within a budget.

    120 of 156 lane rows on the node that motivated this work already pointed at
    missing directories, so a per-row event would re-announce the whole backlog
    on every worker restart.
    """
    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()
    backlog = _GHOST_EMIT_BUDGET + 5
    for i in range(backlog):
        register_lane_worktree(
            source_repo=source_repo,
            thread_id=f"ghost-{i}",
            worktree_path=worktree_root / f"lane-ghost-{i}",
            branch_name=f"cursor-sdk/lane-ghost-{i}",
            branch_point="master",
        )
    emitted: list[str] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_prune."
        "emit_sdk_lane_b_registry_ghost_row",
        lambda **kw: emitted.append(kw["worktree_path"]),
    )
    _stub_occupancy(monkeypatch)

    sweep = reap_orphan_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )

    assert sweep.registry_ghost_rows == backlog
    assert len(emitted) == _GHOST_EMIT_BUDGET

    # Second sweep: the backlog is already reported, so it stays quiet while the
    # count keeps telling the truth.
    again = reap_orphan_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )
    assert again.registry_ghost_rows == backlog
    assert len(emitted) == _GHOST_EMIT_BUDGET


def _clean_lane(source_repo: Path, worktree_root: Path, name: str) -> Path:
    """Zero-commit clean ``cursor-sdk/`` worktree (no mint side effects)."""
    worktree_root.mkdir(parents=True, exist_ok=True)
    lane = worktree_root / name
    _git(
        "worktree",
        "add",
        "-b",
        f"cursor-sdk/{name}",
        str(lane),
        "HEAD",
        cwd=source_repo,
    )
    return lane


def _head(source_repo: Path) -> str:
    return _git("rev-parse", "HEAD", cwd=source_repo).stdout.strip()


def _capture_removed(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    removed: list[dict] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_events.emit_sdk_lane_b_worktree_removed",
        lambda **kwargs: removed.append(kwargs),
    )
    return removed


def _hide_skip_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshot helpers return empty, as if the SELECT ran before the insert."""
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_reconcile._registered_worktree_paths",
        lambda: set(),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_gc.registered_branch_names",
        lambda: set(),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_live_guard.live_ledger_worktree_paths",
        lambda **_k: set(),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_live_guard.live_bridge_worktree_paths",
        lambda **_k: set(),
    )


def test_reconcile_reread_keeps_row_committed_during_the_pass(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skip-set miss plus a live registry and ledger row must not remove the tree.

    Occupancy cache is pre-filled with no bridge. The row is inserted before
    the call; the snapshot helpers are forced empty so only the pre-remove
    reread can see it.
    """
    import time

    from services.git_integration_worker import cursor_sdk_orphan

    worktree_root = tmp_path / "worktrees"
    lane = _clean_lane(source_repo, worktree_root, "lane-reread")
    dispatch_id = "reread-live"
    thread_id = "reread-thread"
    register_lane_worktree(
        source_repo=source_repo,
        thread_id=thread_id,
        worktree_path=lane,
        branch_name="cursor-sdk/lane-reread",
        branch_point=_head(source_repo),
        last_dispatch_id=dispatch_id,
    )
    _admit(
        ledger=CursorDispatchLedger.instance(),
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        source_repo=source_repo,
        lease_key=str(lane.resolve()),
    )
    _hide_skip_set(monkeypatch)
    _stub_occupancy(monkeypatch)
    cursor_sdk_orphan._occupancy_cache = (time.monotonic(), [])
    removed = _capture_removed(monkeypatch)

    reconciled, surfaced = reconcile_unregistered_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )

    assert (reconciled, surfaced) == (0, 0)
    assert lane.is_dir()
    assert removed == []


def test_reconcile_removes_unregistered_terminal_lane(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean tree with no registry row and a terminal ledger row is removed."""
    worktree_root = tmp_path / "worktrees"
    lane = _clean_lane(source_repo, worktree_root, "lane-terminal")
    dispatch_id = "terminal-row"
    _admit(
        ledger=CursorDispatchLedger.instance(),
        dispatch_id=dispatch_id,
        thread_id="terminal-thread",
        source_repo=source_repo,
        lease_key=str(lane.resolve()),
    )
    CursorDispatchLedger.instance().mark_terminal(
        dispatch_id=dispatch_id,
        terminal_status="completed",
    )
    _stub_occupancy(monkeypatch)
    removed = _capture_removed(monkeypatch)

    reconciled, surfaced = reconcile_unregistered_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )

    assert (reconciled, surfaced) == (1, 0)
    assert not lane.exists()
    assert removed


def test_reconcile_fresh_bridge_roster_keeps_unregistered_lane(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bridge that appears only on ``fresh=True`` holds the tree.

    The cached roster is empty. ``live_bridge_occupancy(fresh=False)`` stays
    empty; ``fresh=True`` reports the lane.
    """
    import time

    from services.git_integration_worker import cursor_sdk_orphan

    worktree_root = tmp_path / "worktrees"
    lane = _clean_lane(source_repo, worktree_root, "lane-fresh")
    bridge = BridgeOccupancy(pid=4242, cwd=str(lane), dispatch_id="fresh-bridge")

    def scan(*_a, fresh: bool = False, **_k):
        if fresh:
            return [bridge]
        return []

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_orphan.live_bridge_occupancy",
        scan,
    )
    reset_occupancy_cache()
    cursor_sdk_orphan._occupancy_cache = (time.monotonic(), [])
    removed = _capture_removed(monkeypatch)

    reconciled, surfaced = reconcile_unregistered_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )

    assert (reconciled, surfaced) == (0, 0)
    assert lane.is_dir()
    assert removed == []


def test_reconcile_reread_failure_keeps_the_tree(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable ledger at reread time refuses the remove."""
    worktree_root = tmp_path / "worktrees"
    lane = _clean_lane(source_repo, worktree_root, "lane-unreadable")
    _hide_skip_set(monkeypatch)
    _stub_occupancy(monkeypatch)

    def _boom():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_live_guard.ledger_connection",
        _boom,
    )
    removed = _capture_removed(monkeypatch)

    reconciled, surfaced = reconcile_unregistered_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )

    assert (reconciled, surfaced) == (0, 0)
    assert lane.is_dir()
    assert removed == []
