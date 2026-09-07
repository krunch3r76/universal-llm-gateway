"""AC-W0: admit-binding provenance via ``AdmitBindingResult.binding_kind``."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_worktree import (
    AdmitBindingResult,
    lane_worktree_dir,
    mint_dispatch_worktree,
    resolve_admit_binding,
)
from services.git_integration_worker.cursor_sdk_worktree_prune import (
    rollback_dispatch_worktree,
    sibling_non_terminal_dispatch_on_thread,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    lookup_lane_worktree,
    unregister_lane_worktree,
)
from services.git_integration_worker import cursor_sdk_events
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
        "thread_id": "t-bind",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-bind",
        "execution_id": "exec-disp-bind",
        "message": "hello",
        "lane": "B",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def test_ac_w0_minted_on_fresh_lane(source_repo: Path, tmp_path: Path) -> None:
    worktree_root = tmp_path / "worktrees"
    binding = resolve_admit_binding(
        req=_req(dispatch_id="mint-1"),
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    assert isinstance(binding, AdmitBindingResult)
    assert binding.binding_kind == "minted"
    assert binding.workspace == lane_worktree_dir(worktree_root, "t-bind").resolve()


def test_ac_w0_reused_on_second_dispatch(source_repo: Path, tmp_path: Path) -> None:
    worktree_root = tmp_path / "worktrees"
    first = resolve_admit_binding(
        req=_req(dispatch_id="reuse-1"),
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    second = resolve_admit_binding(
        req=_req(dispatch_id="reuse-2", execution_id="exec-reuse-2"),
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    assert first.binding_kind == "minted"
    assert second.binding_kind == "reused"
    assert second.workspace == first.workspace


def test_ac_w0_adopted_on_unregistered_existing_dir(
    source_repo: Path, tmp_path: Path
) -> None:
    worktree_root = tmp_path / "worktrees"
    orphan = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id="orphan-adopt",
        thread_id="t-adopt",
    )
    unregister_lane_worktree(thread_id="t-adopt")

    binding = resolve_admit_binding(
        req=_req(dispatch_id="adopt-1", thread_id="t-adopt"),
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    assert binding.binding_kind == "adopted"
    assert binding.workspace == orphan.resolve()
    assert lookup_lane_worktree(thread_id="t-adopt") is not None


def test_ac_w0_lane_a_binding_kind(source_repo: Path, tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    binding = resolve_admit_binding(
        req=_req(lane="A"),
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=tmp_path / "worktrees",
        dispatch_workspace_default=shared,
        lane="A",
    )
    assert binding.binding_kind == "lane_a"
    assert binding.workspace == shared


def test_ac_w0_rollback_refuses_sibling_non_terminal(
    source_repo: Path, tmp_path: Path
) -> None:
    worktree_root = tmp_path / "worktrees"
    thread_id = "t-sibling"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id="rollback-me",
        thread_id=thread_id,
    )
    ledger = CursorDispatchLedger.instance()
    sibling_req = _req(
        dispatch_id="sibling-live",
        thread_id=thread_id,
        execution_id="exec-sibling",
    )
    ledger.admit(
        req=sibling_req,
        fingerprint="fp-sibling",
        execution_id="exec-sibling",
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id="sibling-live",
            thread_id=thread_id,
            model_id="composer-2.5",
        ),
        source_repo=str(source_repo.resolve()),
        lease_key=str(wt.resolve()),
        contract="implement",
        worker_instance="worker-a",
    )
    assert (
        sibling_non_terminal_dispatch_on_thread(
            thread_id=thread_id,
            exclude_dispatch_id="rollback-me",
        )
        == "sibling-live"
    )
    result = rollback_dispatch_worktree(
        dispatch_id="rollback-me",
        thread_id=thread_id,
        source_repo=source_repo,
    )
    assert not result.pruned
    assert wt.is_dir()


def test_ac_w0_7_admit_bound_emits_binding_kind(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-W0-7: admit path emits ``sdk.lane_b.admit_bound`` with ``binding_kind``."""
    worktree_root = tmp_path / "worktrees"
    binding = resolve_admit_binding(
        req=_req(dispatch_id="admit-bound-1", thread_id="t-admit-bound"),
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_events.emit_sdk_lane_b_admit_bound",
        lambda **kwargs: emitted.append(dict(kwargs)),
    )
    cursor_sdk_events.emit_sdk_lane_b_admit_bound(
        dispatch_id="admit-bound-1",
        thread_id="t-admit-bound",
        binding_kind=binding.binding_kind,
        worktree_path=str(binding.workspace),
    )
    assert len(emitted) == 1
    assert emitted[0]["binding_kind"] == "minted"
    assert emitted[0]["thread_id"] == "t-admit-bound"
    assert emitted[0]["dispatch_id"] == "admit-bound-1"
