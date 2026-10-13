"""Branch debt records satellite workspace identity for discharge resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from services.git_integration_worker.cursor_sdk_branch_debt import (
    get_branch_debt,
    open_branch_debt,
    resolve_debt_source_repo,
    workspace_token_for_repo,
)
from services.git_integration_worker.cursor_sdk_branch_discharge import (
    discharge_discard,
)
from services.git_integration_worker.cursor_sdk_branch_terminal import (
    settle_lane_branch,
)
from services.git_integration_worker.cursor_sdk_worktree_reconcile import (
    reconcile_unregistered_worktrees,
)


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


def _branch(repo: Path, name: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "branch", name],
        check=True,
        capture_output=True,
    )


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _branch_with_change(repo: Path, *, branch: str, path: str, content: str) -> None:
    _git("checkout", "-b", branch, cwd=repo)
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git("add", path, cwd=repo)
    _git("commit", "-m", branch, cwd=repo)
    _git("checkout", "master", cwd=repo)


def _seed_dispatch(
    *,
    dispatch_id: str,
    thread_id: str,
    source_repo: str,
) -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id=thread_id,
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message="test",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            model_id="composer-2.5",
        ),
        source_repo=source_repo,
        contract="implement",
        worker_instance="worker-test",
    )
    ledger.mark_running(dispatch_id=dispatch_id)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


@pytest.fixture
def projects_layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    hub = projects_root / "hub-gateway"
    hub.mkdir()
    _init_git_repo(hub)
    satellite = projects_root / "sat-bot"
    satellite.mkdir()
    _init_git_repo(satellite)
    roster_file = hub / "cursor-plugins/ulg-ecosystem/SATELLITES.txt"
    roster_file.parent.mkdir(parents=True)
    roster_file.write_text("sat-bot\n", encoding="utf-8")
    return hub, satellite, projects_root


def test_workspace_token_for_repo_satellite_and_hub(
    projects_layout: tuple[Path, Path, Path],
) -> None:
    hub, satellite, projects_root = projects_layout
    assert workspace_token_for_repo(hub, hub=hub, projects_root=projects_root) is None
    assert (
        workspace_token_for_repo(satellite, hub=hub, projects_root=projects_root)
        == "sat-bot"
    )


def test_resolve_debt_source_repo_legacy_and_satellite(
    projects_layout: tuple[Path, Path, Path],
) -> None:
    hub, satellite, projects_root = projects_layout
    assert (
        resolve_debt_source_repo(None, hub=hub, projects_root=projects_root)
        == hub.resolve()
    )
    assert (
        resolve_debt_source_repo("sat-bot", hub=hub, projects_root=projects_root)
        == satellite.resolve()
    )


def test_open_branch_debt_stores_source_repo_token(
    projects_layout: tuple[Path, Path, Path],
) -> None:
    debt = open_branch_debt(
        branch_name="cursor-sdk/lane-sat",
        thread_id="9953",
        dispatch_id="d-9953",
        source_repo="sat-bot",
    )
    assert debt.source_repo == "sat-bot"
    loaded = get_branch_debt(branch_name="cursor-sdk/lane-sat")
    assert loaded is not None
    assert loaded.source_repo == "sat-bot"


def test_discharge_uses_satellite_repo_from_debt_row(
    projects_layout: tuple[Path, Path, Path],
) -> None:
    hub, satellite, projects_root = projects_layout
    branch = "cursor-sdk/lane-sat-discharge"
    _branch(satellite, branch)
    open_branch_debt(
        branch_name=branch,
        thread_id="9953",
        dispatch_id="d-9953-discharge",
        source_repo="sat-bot",
    )
    repo = resolve_debt_source_repo("sat-bot", hub=hub, projects_root=projects_root)
    result = discharge_discard(
        repo=repo,
        branch_name=branch,
        reason="test satellite discharge",
    )
    assert result.discharged is True
    debt = get_branch_debt(branch_name=branch)
    assert debt is not None
    assert debt.discharged_at is not None


def test_settle_lane_branch_records_workspace_token(
    projects_layout: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub, satellite, projects_root = projects_layout
    branch = "cursor-sdk/lane-settle"
    _branch_with_change(satellite, branch=branch, path="settle.py", content="s = 1\n")
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(hub))
    monkeypatch.setenv("GIT_INTEGRATION_DISPATCH_WORKSPACE", str(projects_root))
    _seed_dispatch(
        dispatch_id="test-dispatch",
        thread_id="9953",
        source_repo=str(satellite.resolve()),
    )
    settlement = settle_lane_branch(
        source_repo=satellite,
        branch_name=branch,
        thread_id="9953",
        dispatch_id="test-dispatch",
        closeout_text=None,
        commits_ahead=1,
        landed=None,
        head_sha=None,
    )
    assert settlement.outcome == "debt_opened"
    debt = get_branch_debt(branch_name=branch)
    assert debt is not None
    assert debt.source_repo == "sat-bot"


def test_open_debt_uses_ledger_satellite_not_hub(
    projects_layout: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closeout settle passes hub; debt token comes from the dispatch ledger row."""
    hub, satellite, projects_root = projects_layout
    branch = "cursor-sdk/lane-ledger-sat"
    _branch_with_change(
        satellite, branch=branch, path="ledger_sat.py", content="l = 1\n"
    )
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(hub))
    monkeypatch.setenv("GIT_INTEGRATION_DISPATCH_WORKSPACE", str(projects_root))
    _seed_dispatch(
        dispatch_id="d-ledger-sat",
        thread_id="9910",
        source_repo=str(satellite.resolve()),
    )
    settlement = settle_lane_branch(
        source_repo=hub,
        branch_name=branch,
        thread_id="9910",
        dispatch_id="d-ledger-sat",
        closeout_text=None,
        commits_ahead=1,
        landed=None,
        head_sha=None,
    )
    assert settlement.outcome == "debt_opened"
    debt = get_branch_debt(branch_name=branch)
    assert debt is not None
    assert debt.source_repo == "sat-bot"


def test_hub_settle_leaves_source_repo_null(
    projects_layout: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: hub dispatch ledger row yields NULL debt source_repo at settle.

    Does not assert which hub guard fired; ``workspace_token_for_repo(hub)``
    also returns None without ``_source_is_hub``.
    """
    hub, satellite, projects_root = projects_layout
    branch = "cursor-sdk/lane-hub-null"
    _branch_with_change(hub, branch=branch, path="hub_null.py", content="h = 1\n")
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(hub))
    monkeypatch.setenv("GIT_INTEGRATION_DISPATCH_WORKSPACE", str(projects_root))
    _seed_dispatch(
        dispatch_id="d-hub-null",
        thread_id="9911",
        source_repo=str(hub.resolve()),
    )
    settlement = settle_lane_branch(
        source_repo=hub,
        branch_name=branch,
        thread_id="9911",
        dispatch_id="d-hub-null",
        closeout_text=None,
        commits_ahead=1,
        landed=None,
        head_sha=None,
    )
    assert settlement.outcome == "debt_opened"
    debt = get_branch_debt(branch_name=branch)
    assert debt is not None
    assert debt.source_repo is None


def test_reopen_fills_empty_source_repo_keeps_opened_at(
    projects_layout: tuple[Path, Path, Path],
) -> None:
    branch = "cursor-sdk/lane-reopen-fill"
    first = open_branch_debt(
        branch_name=branch,
        thread_id="9912",
        dispatch_id="d-reopen",
        source_repo=None,
    )
    second = open_branch_debt(
        branch_name=branch,
        thread_id="9912",
        dispatch_id="d-reopen",
        source_repo="sat-bot",
    )
    assert second.source_repo == "sat-bot"
    assert second.opened_at == first.opened_at


def test_missing_checkout_leaves_source_repo_null(
    projects_layout: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub, _satellite, projects_root = projects_layout
    branch = "cursor-sdk/lane-missing-checkout"
    _branch_with_change(hub, branch=branch, path="missing.py", content="m = 1\n")
    missing = projects_root / "gone-satellite"
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(hub))
    monkeypatch.setenv("GIT_INTEGRATION_DISPATCH_WORKSPACE", str(projects_root))
    _seed_dispatch(
        dispatch_id="d-missing",
        thread_id="9913",
        source_repo=str(missing.resolve()),
    )
    settlement = settle_lane_branch(
        source_repo=hub,
        branch_name=branch,
        thread_id="9913",
        dispatch_id="d-missing",
        closeout_text=None,
        commits_ahead=1,
        landed=None,
        head_sha=None,
    )
    assert settlement.outcome == "debt_opened"
    debt = get_branch_debt(branch_name=branch)
    assert debt is not None
    assert debt.source_repo is None


def test_dirty_unregistered_worktree_stores_satellite_token(
    projects_layout: tuple[Path, Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub, satellite, projects_root = projects_layout
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(hub))
    monkeypatch.setenv("GIT_INTEGRATION_DISPATCH_WORKSPACE", str(projects_root))
    root = tmp_path / "worktrees"
    root.mkdir()
    branch = "cursor-sdk/lane-dirty-sat"
    _branch_with_change(
        satellite, branch=branch, path="dirty_sat.py", content="x = 1\n"
    )
    tree = root / "dirty-sat"
    _git("worktree", "add", str(tree), branch, cwd=satellite)
    (tree / "wip.py").write_text("wip\n", encoding="utf-8")

    reconciled, surfaced = reconcile_unregistered_worktrees(
        source_repo=satellite,
        worktree_root=root,
    )
    assert (reconciled, surfaced) == (0, 1)
    debt = get_branch_debt(branch_name=branch)
    assert debt is not None
    assert debt.source_repo == "sat-bot"
