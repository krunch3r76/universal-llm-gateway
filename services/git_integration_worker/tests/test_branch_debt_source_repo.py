"""Branch debt records satellite workspace identity for discharge resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
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
    _branch(satellite, branch)
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(hub))
    monkeypatch.setenv("GIT_INTEGRATION_DISPATCH_WORKSPACE", str(projects_root))
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
