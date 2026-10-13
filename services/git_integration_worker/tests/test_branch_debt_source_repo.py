"""Branch debt records satellite workspace identity for discharge resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from implement_admission.closeout_models import EffectsManifest

from services.git_integration_worker.config import load_config
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
from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding
from services.git_integration_worker.cursor_sdk_closeout import SdkRunOutcome
from services.git_integration_worker.cursor_sdk_closeout.delivery_assembly.orchestration import (
    _assemble_closeout_delivery,
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


def _commit_on_branch(repo: Path, branch: str, rel_path: str, content: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "checkout", branch],
        check=True,
        capture_output=True,
    )
    path = repo / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", rel_path],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", f"lane work on {branch}"],
        check=True,
        capture_output=True,
    )


def test_settle_lane_branch_records_workspace_token(
    projects_layout: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub, satellite, projects_root = projects_layout
    branch = "cursor-sdk/lane-settle"
    _branch(satellite, branch)
    _commit_on_branch(satellite, branch, "lane-settle.txt", "ahead\n")
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


def test_assemble_closeout_settles_satellite_debt_against_write_tree(
    projects_layout: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production passes hub as source_repo; branch debt must use the satellite write tree."""
    hub, satellite, projects_root = projects_layout
    branch = "cursor-sdk/lane-closeout-sat"
    _branch(satellite, branch)
    _commit_on_branch(satellite, branch, "closeout-sat.txt", "ahead\n")
    tip = subprocess.run(
        ["git", "-C", str(satellite), "rev-parse", branch],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(hub))
    monkeypatch.setenv("GIT_INTEGRATION_DISPATCH_WORKSPACE", str(projects_root))
    cfg = load_config()
    binding = CaptureBinding.lane_a(cfg, dispatch_source_repo=satellite)

    def _stub_lane_settle(**_kwargs: object) -> tuple:
        return (
            "B",
            branch,
            None,
            tip,
            1,
            1,
            False,
            "B",
            False,
            "",
            False,
            tip,
            [],
            None,
            None,
        )

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.delivery_assembly."
        "lane_settlement.settle_lane_and_dispatch_fields",
        _stub_lane_settle,
    )
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=1,
        tool_call_count=0,
        effects_manifest=EffectsManifest(
            dispatch_id="d-closeout-sat",
            thread_id="16518",
            capture_sources=["conversation"],
            surfaces={},
        ),
    )
    _assemble_closeout_delivery(
        source_repo=hub,
        binding=binding,
        dispatch_id="d-closeout-sat",
        outcome=outcome,
        degraded_reason=None,
        thread_id="16518",
        work_item_ref=None,
        baseline=None,
        packet_text=None,
        cortex_artifact_paths=[],
        gate_d_created_rels=(),
        finalize_oversize=False,
    )
    debt = get_branch_debt(branch_name=branch)
    assert debt is not None
    assert debt.source_repo == "sat-bot"
