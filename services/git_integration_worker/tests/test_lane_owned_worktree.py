"""Lane-owned worktree: sticky bind, per-branch lease, sequential visibility."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_lane_select import select_lane
from services.git_integration_worker.cursor_sdk_worktree import (
    lane_branch_name,
    lane_worktree_dir,
    maybe_prune_worktree_on_terminal,
    mint_dispatch_worktree,
    resolve_admit_binding,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    lookup_lane_worktree,
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
    yield
    CursorDispatchLedger._instance = None


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


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "t-lane",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-1",
        "execution_id": "exec-disp-1",
        "message": "hello",
        "lane": "B",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit(
    ledger: CursorDispatchLedger,
    req: CursorDispatchRequest,
    *,
    source_repo: str,
    lease_key: str,
) -> CursorDispatchResponse | None:
    return ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        source_repo=source_repo,
        lease_key=lease_key,
        contract="implement",
        read_only=False,
        write_lease_slot_limit=1,
    )


def test_second_dispatch_reuses_lane_tree(source_repo: Path, tmp_path: Path) -> None:
    worktree_root = tmp_path / "worktrees"
    first, key1 = resolve_admit_binding(
        req=_req(dispatch_id="disp-1"),
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    second, key2 = resolve_admit_binding(
        req=_req(dispatch_id="disp-2", execution_id="exec-disp-2"),
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    assert first == second
    assert key1 == key2
    assert first == lane_worktree_dir(worktree_root, "t-lane").resolve()
    record = lookup_lane_worktree(thread_id="t-lane")
    assert record is not None
    assert record.branch_name == lane_branch_name("t-lane")
    assert record.last_dispatch_id == "disp-2"


def test_distinct_lanes_get_distinct_trees(source_repo: Path, tmp_path: Path) -> None:
    worktree_root = tmp_path / "worktrees"
    a, key_a = resolve_admit_binding(
        req=_req(thread_id="lane-a", dispatch_id="da"),
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    b, key_b = resolve_admit_binding(
        req=_req(thread_id="lane-b", dispatch_id="db", execution_id="exec-db"),
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    assert a != b
    assert key_a != key_b


def test_same_lane_write_lease_queues(source_repo: Path, tmp_path: Path) -> None:
    worktree_root = tmp_path / "worktrees"
    workspace, lease_key = resolve_admit_binding(
        req=_req(dispatch_id="holder"),
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    ledger = CursorDispatchLedger.instance()
    repo = str(source_repo.resolve())
    _admit(
        ledger,
        _req(dispatch_id="holder", thread_id="t-lane"),
        source_repo=repo,
        lease_key=lease_key,
    )
    queued = _admit(
        ledger,
        _req(
            dispatch_id="waiter",
            thread_id="t-lane",
            execution_id="exec-waiter",
            message="waiter work",
        ),
        source_repo=repo,
        lease_key=lease_key,
    )
    assert queued is not None
    assert queued.status == "queued"
    snap = ledger.lease_snapshot(lease_key=lease_key)
    assert snap["queued"][0]["queued_on"] == f"write_lease:{lease_key}"
    _ = workspace


def test_unassociated_thread_stays_lane_a(source_repo: Path, tmp_path: Path) -> None:
    lane, _advisories, reason = select_lane(
        req=_req(lane=None, message="consult"),
        regime_active=True,
        source_repo=source_repo,
        files_expected=[],
        contract="consult",
        lane_worktree=None,
    )
    assert lane == "A"
    assert reason == "opt_out"


def test_existing_lane_tree_binds_empty_files_expected(
    source_repo: Path, tmp_path: Path
) -> None:
    worktree_root = tmp_path / "worktrees"
    workspace, _key = resolve_admit_binding(
        req=_req(dispatch_id="first"),
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    lane, _advisories, reason = select_lane(
        req=_req(lane=None, dispatch_id="second", message="consult"),
        regime_active=True,
        source_repo=source_repo,
        files_expected=[],
        contract="consult",
        lane_worktree=workspace,
    )
    assert lane == "B"
    assert reason == "lane_bound"


def test_lane_tree_survives_terminal(source_repo: Path, tmp_path: Path) -> None:
    worktree_root = tmp_path / "worktrees"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id="term-1",
        thread_id="t-survive",
    )
    result = maybe_prune_worktree_on_terminal(
        dispatch_id="term-1",
        source_repo=source_repo,
    )
    assert not result.pruned
    assert wt.is_dir()
    assert lookup_lane_worktree(thread_id="t-survive") is not None


def test_sequential_lane_work_is_visible(source_repo: Path, tmp_path: Path) -> None:
    worktree_root = tmp_path / "worktrees"
    first, _key = resolve_admit_binding(
        req=_req(dispatch_id="n1"),
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    (first / "from_n1.py").write_text("n1\n", encoding="utf-8")
    _git("add", "from_n1.py", cwd=first)
    _git("commit", "-m", "n1 work", cwd=first)
    second, _key2 = resolve_admit_binding(
        req=_req(dispatch_id="n2", execution_id="exec-n2"),
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    assert second == first
    assert (second / "from_n1.py").read_text(encoding="utf-8") == "n1\n"
