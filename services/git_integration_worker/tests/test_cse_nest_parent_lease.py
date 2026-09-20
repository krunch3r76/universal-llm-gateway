"""CSE ``nest_under`` parent lease resolution for worktree mint."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from claude_bundles.holder_strings import format_nest_under_cse

from services.git_integration_worker.cse_session_holders import (
    ensure_schema,
    upsert_holder,
)
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_worktree import (
    WorktreeMintError,
    pin_lane_worktree_on_admit,
    resolve_admit_binding,
)
from services.git_integration_worker.cursor_sdk_worktree_lock import (
    ForeignLockError,
    list_locked_worktrees,
    lock_lane_worktree,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

_CSE_URL = "https://claude.ai/cowork/cse_nestlease1"


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
    subprocess.run(
        ["git", "init", "-b", "master"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=str(repo), check=True)
    return repo


def _seed_driving_holder(
    ledger: CursorDispatchLedger,
    *,
    lane_thread_id: str,
) -> str:
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(
            conn,
            chat_url=_CSE_URL,
            registration_id="reg-nest-lease",
            lane_thread_id=lane_thread_id,
        )
        conn.commit()
    return format_nest_under_cse("cse_nestlease1")


def test_cse_nest_hub_fallback_when_lane_has_no_worktree(
    source_repo: Path, tmp_path: Path
) -> None:
    ledger = CursorDispatchLedger.instance()
    nest_under = _seed_driving_holder(ledger, lane_thread_id="11667")
    binding = resolve_admit_binding(
        req=CursorDispatchRequest(
            thread_id="11668",
            model="cursor/composer-2.5",
            dispatch_id="child-hub",
            execution_id="exec-child-hub",
            message="nested under cse without lane tree",
            nest_under=nest_under,
        ),
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=tmp_path / "worktrees",
        dispatch_workspace_default=source_repo.parent,
        lane="A",
    )
    assert binding.binding_kind == "nested"
    assert binding.lease_key == str(source_repo.resolve())
    assert binding.workspace == source_repo.resolve()


def test_cse_nest_inherits_lane_worktree_when_present(
    source_repo: Path, tmp_path: Path
) -> None:
    ledger = CursorDispatchLedger.instance()
    lane_thread_id = "11667"
    nest_under = _seed_driving_holder(ledger, lane_thread_id=lane_thread_id)
    parent_req = CursorDispatchRequest(
        thread_id=lane_thread_id,
        model="cursor/composer-2.5",
        dispatch_id="lane-parent",
        execution_id="exec-lane-parent",
        message="lane b parent",
        lane="B",
    )
    parent_binding = resolve_admit_binding(
        req=parent_req,
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=tmp_path / "worktrees",
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    child_binding = resolve_admit_binding(
        req=CursorDispatchRequest(
            thread_id="11668",
            model="cursor/composer-2.5",
            dispatch_id="child-lane",
            execution_id="exec-child-lane",
            message="nested under cse with lane tree",
            nest_under=nest_under,
        ),
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=tmp_path / "worktrees",
        dispatch_workspace_default=source_repo.parent,
        lane="A",
    )
    assert child_binding.binding_kind == "nested"
    assert child_binding.lease_key == parent_binding.lease_key
    assert child_binding.workspace == parent_binding.workspace


def test_cse_nest_unknown_parent_raises_mint_error(
    source_repo: Path, tmp_path: Path
) -> None:
    with pytest.raises(WorktreeMintError, match="nest parent not found"):
        resolve_admit_binding(
            req=CursorDispatchRequest(
                thread_id="11668",
                model="cursor/composer-2.5",
                dispatch_id="child-miss",
                execution_id="exec-child-miss",
                message="missing cse parent",
                nest_under="cse:cse_missing",
            ),
            source_repo=source_repo,
            hub=source_repo,
            worktree_root=tmp_path / "worktrees",
            dispatch_workspace_default=source_repo.parent,
            lane="A",
        )


def test_cse_nest_pin_inherits_holder_lane_lock(
    source_repo: Path, tmp_path: Path
) -> None:
    """Lock held by holder lane thread + cse nest child thread does not 503 pin."""
    ledger = CursorDispatchLedger.instance()
    lane_thread_id = "11667"
    child_thread_id = "11828"
    nest_under = _seed_driving_holder(ledger, lane_thread_id=lane_thread_id)
    parent_req = CursorDispatchRequest(
        thread_id=lane_thread_id,
        model="cursor/composer-2.5",
        dispatch_id="lane-parent-pin",
        execution_id="exec-lane-parent-pin",
        message="lane b parent",
        lane="B",
    )
    parent_binding = resolve_admit_binding(
        req=parent_req,
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=tmp_path / "worktrees",
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    parent_lock = lock_lane_worktree(
        source_repo,
        parent_binding.workspace,
        dispatch_id=parent_req.dispatch_id,
        thread_id=lane_thread_id,
    )
    child_binding = resolve_admit_binding(
        req=CursorDispatchRequest(
            thread_id=child_thread_id,
            model="cursor/composer-2.5",
            dispatch_id="child-cse-pin",
            execution_id="exec-child-cse-pin",
            message="nested under cse with lane tree",
            nest_under=nest_under,
        ),
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=tmp_path / "worktrees",
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    assert child_binding.workspace == parent_binding.workspace
    pin_lane_worktree_on_admit(
        source_repo=source_repo,
        thread_id=child_thread_id,
        dispatch_id="child-cse-pin",
        worktree_path=child_binding.workspace,
        inherit_lane_thread_id=lane_thread_id,
    )
    locked = list_locked_worktrees(source_repo)
    assert len(locked) == 1
    assert locked[0].parsed is not None
    assert locked[0].parsed.thread_id == lane_thread_id
    assert locked[0].reason == parent_lock.lock_reason


def test_cse_nest_pin_still_refuses_unrelated_foreign_lock(
    source_repo: Path, tmp_path: Path
) -> None:
    ledger = CursorDispatchLedger.instance()
    lane_thread_id = "11667"
    nest_under = _seed_driving_holder(ledger, lane_thread_id=lane_thread_id)
    parent_req = CursorDispatchRequest(
        thread_id=lane_thread_id,
        model="cursor/composer-2.5",
        dispatch_id="lane-parent-foreign",
        execution_id="exec-lane-parent-foreign",
        message="lane b parent",
        lane="B",
    )
    parent_binding = resolve_admit_binding(
        req=parent_req,
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=tmp_path / "worktrees",
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    lock_lane_worktree(
        source_repo,
        parent_binding.workspace,
        dispatch_id="foreign-disp",
        thread_id="99999",
    )
    child_binding = resolve_admit_binding(
        req=CursorDispatchRequest(
            thread_id="11828",
            model="cursor/composer-2.5",
            dispatch_id="child-cse-foreign",
            execution_id="exec-child-cse-foreign",
            message="nested under cse",
            nest_under=nest_under,
        ),
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=tmp_path / "worktrees",
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    with pytest.raises(ForeignLockError):
        pin_lane_worktree_on_admit(
            source_repo=source_repo,
            thread_id="11828",
            dispatch_id="child-cse-foreign",
            worktree_path=child_binding.workspace,
            inherit_lane_thread_id=lane_thread_id,
        )


def test_dispatch_id_nest_under_still_inherits_parent_lease(
    source_repo: Path, tmp_path: Path
) -> None:
    ledger = CursorDispatchLedger.instance()
    parent_req = CursorDispatchRequest(
        thread_id="6661",
        model="cursor/composer-2.5",
        dispatch_id="parent-dispatch",
        execution_id="exec-parent-dispatch",
        message="sdk parent",
        lane="B",
    )
    parent_binding = resolve_admit_binding(
        req=parent_req,
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=tmp_path / "worktrees",
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    ledger.admit(
        req=parent_req,
        fingerprint=ledger.fingerprint(parent_req),
        execution_id=parent_req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=parent_req.dispatch_id,
            thread_id=parent_req.thread_id,
            model_id="composer-2.5",
        ),
        source_repo=str(source_repo.resolve()),
        lease_key=parent_binding.lease_key,
        contract="implement",
        worker_instance="worker-a",
    )
    child_binding = resolve_admit_binding(
        req=CursorDispatchRequest(
            thread_id="6661",
            model="cursor/composer-2.5",
            dispatch_id="child-dispatch",
            execution_id="exec-child-dispatch",
            message="sdk nested child",
            nest_under="parent-dispatch",
        ),
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=tmp_path / "worktrees",
        dispatch_workspace_default=source_repo.parent,
        lane="A",
    )
    assert child_binding.binding_kind == "nested"
    assert child_binding.lease_key == parent_binding.lease_key
    assert child_binding.workspace == parent_binding.workspace
