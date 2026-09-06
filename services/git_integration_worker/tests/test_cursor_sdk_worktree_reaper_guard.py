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
import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_orphan import BridgeOccupancy
from services.git_integration_worker.cursor_sdk_worktree import (
    mint_dispatch_worktree,
    reap_orphan_worktrees,
)
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
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
    reset_ghost_row_reports()
    reset_occupancy_cache()
    yield
    CursorDispatchLedger._instance = None
    reset_ghost_row_reports()
    reset_occupancy_cache()


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
        lambda: list(bridges),
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
    assert lookup_lane_worktree(thread_id=dispatch_id) is not None
    assert worktree_held_by_live_bridge(worktree_path=wt) == 4242


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
    unregister_lane_worktree(thread_id=dispatch_id)
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
    unregister_lane_worktree(thread_id=dispatch_id)
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
    assert lookup_lane_worktree(thread_id=dispatch_id) is not None


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
