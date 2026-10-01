"""Lane-B ``land_disposition: unlanded <sha>`` grading and hub-master landed resolution."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from implement_admission.spec import CloseoutStatus, WorkOutcome

from services.git_integration_worker.relay.closeout_plane_probe import (
    probe_three_planes,
)
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_branch_discharge import (
    DISCHARGE_DISCARD,
    DISCHARGE_LANDED,
    DISCHARGE_UNLANDED,
)
from services.git_integration_worker.cursor_sdk_branch_terminal import (
    parse_land_disposition,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    build_implement_closeout_body,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    sidecar_workspaces_ref,
)
from services.git_integration_worker.cursor_sdk_deliverables_expected import (
    admit_landed_true,
)
from services.git_integration_worker.cursor_sdk_hub_land_scope import (
    packet_hub_land_scoped_out,
)
from services.git_integration_worker.cursor_sdk_land_discipline import (
    LANE_B_UNLANDED_DEVIATION,
    apply_lane_b_land_incompleteness,
)
from services.git_integration_worker.cursor_sdk_lane_b_commit import branch_state


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


def test_enum_quotes_after_edit() -> None:
    """AC1 — closeout contract enumerates landed, discard, unlanded."""
    assert DISCHARGE_LANDED == "landed"
    assert DISCHARGE_DISCARD == "discard"
    assert DISCHARGE_UNLANDED == "unlanded"


def test_packet_hub_land_scoped_out_do_not_hub_land() -> None:
    assert packet_hub_land_scoped_out("intent: (6) do NOT hub-land; leave lane-B tip")
    assert not packet_hub_land_scoped_out("files_expected: none — land only")


@pytest.mark.parametrize(
    ("packet", "closeout", "expect_deviation"),
    [
        (
            "out-of-scope: hub land\n",
            "land_disposition: unlanded abcdef0123456789abcdef0123456789abcdef0\n",
            False,
        ),
        (
            "intent: land to master before close\n",
            "land_disposition: unlanded abcdef0123456789abcdef0123456789abcdef0\n",
            True,
        ),
    ],
)
def test_apply_lane_b_land_incompleteness_unlanded_grading(
    tmp_path: Path,
    packet: str,
    closeout: str,
    expect_deviation: bool,
) -> None:
    """AC1 grading trace — scoped-out unlanded vs land-in-scope partial."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)
    branch = "cursor-sdk/lane-13063"
    _git("branch", branch, cwd=repo)
    (repo / "work.py").write_text("x\n", encoding="utf-8")
    _git("add", "work.py", cwd=repo)
    env = {**dict(__import__("os").environ)}
    subprocess.run(
        ["git", "-C", str(repo), "checkout", branch],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "lane work"],
        check=True,
        capture_output=True,
        env=env,
    )
    tip = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    closeout_line = closeout.replace("abcdef0123456789abcdef0123456789abcdef0", tip)

    status, deviations = apply_lane_b_land_incompleteness(
        CloseoutStatus.COMPLETE,
        lane="B",
        landed=False,
        commits_ahead=1,
        deviations=None,
        closeout_text=closeout_line,
        packet_text=packet,
        branch_name=branch,
        hub_repo=repo,
    )
    has_dev = LANE_B_UNLANDED_DEVIATION in (deviations or [])
    if expect_deviation:
        assert status == CloseoutStatus.PARTIAL
        assert has_dev
    else:
        assert status == CloseoutStatus.COMPLETE
        assert not has_dev


def test_build_closeout_unlanded_scoped_out_stays_complete(
    tmp_path: Path,
) -> None:
    """Relay path — unlanded + hub land scoped out ⇒ complete, no land deviation."""
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    offgit = ["cortex://notes/system/threads/fixture-deliverable.md"]
    rel = offgit[0].removeprefix("cortex://")
    path = cortex_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# fixture\n", encoding="utf-8")

    _git("init", "-b", "master", cwd=source_repo)
    _git("config", "user.email", "t@example.com", cwd=source_repo)
    _git("config", "user.name", "t", cwd=source_repo)
    (source_repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=source_repo)
    _git("commit", "-m", "seed", cwd=source_repo)
    branch = "cursor-sdk/lane-enum"
    _git("branch", branch, cwd=source_repo)
    subprocess.run(
        ["git", "-C", str(source_repo), "checkout", branch],
        check=True,
        capture_output=True,
    )
    (source_repo / "mod.py").write_text("v\n", encoding="utf-8")
    _git("add", "mod.py", cwd=source_repo)
    subprocess.run(
        ["git", "-C", str(source_repo), "commit", "-m", "lane"],
        check=True,
        capture_output=True,
    )
    tip = _git("rev-parse", "HEAD", cwd=source_repo).stdout.strip()

    body = build_implement_closeout_body(
        dispatch_id="enum-scoped-out",
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=10,
            tool_call_count=1,
        ),
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("enum-scoped-out"),
        result_bytes=100,
        thread_id="13063",
        work_item_ref=None,
        sidecar_markdown=f"land_disposition: unlanded {tip}\n",
        offgit_deliverable_uris=offgit,
        source_repo=source_repo,
        cortex_root=cortex_root,
        deliverables_expected=True,
        lane="B",
        branch=branch,
        landed=False,
        commits_ahead=1,
        packet_text="do NOT hub-land; leave lane-B tip",
        hub_repo=source_repo,
        work_outcome=WorkOutcome.SHIPPED,
    )
    payload = json.loads(body)
    assert payload["status"] == "complete"
    assert LANE_B_UNLANDED_DEVIATION not in (payload.get("deviations") or [])


def test_content_landed_reports_landed_true_when_lane_tip_differs(
    source_repo: Path,
) -> None:
    """AC1 — hub has cherry-picked work; structured landed true though tip differs."""
    branch = "cursor-sdk/lane-cherry"
    branch_point = _git("rev-parse", "master", cwd=source_repo).stdout.strip()
    _git("branch", branch, cwd=source_repo)
    subprocess.run(
        ["git", "-C", str(source_repo), "checkout", branch],
        check=True,
        capture_output=True,
    )
    (source_repo / "landed.py").write_text("shipped\n", encoding="utf-8")
    _git("add", "landed.py", cwd=source_repo)
    subprocess.run(
        ["git", "-C", str(source_repo), "commit", "-m", "lane"],
        check=True,
        capture_output=True,
    )
    tip = _git("rev-parse", branch, cwd=source_repo).stdout.strip()
    subprocess.run(
        ["git", "-C", str(source_repo), "checkout", "master"],
        check=True,
        capture_output=True,
    )
    (source_repo / "unrelated.md").write_text("moved\n", encoding="utf-8")
    _git("add", "unrelated.md", cwd=source_repo)
    _git("commit", "-m", "master moved", cwd=source_repo)
    _git("cherry-pick", tip, cwd=source_repo)
    master_tip = _git("rev-parse", "master", cwd=source_repo).stdout.strip()
    assert master_tip != tip
    state = branch_state(
        source_repo,
        branch_name=branch,
        branch_point=branch_point,
    )
    assert state.content_landed
    plane = probe_three_planes(
        source_repo, head_sha=tip, branch=branch, hub_repo=source_repo
    )
    assert plane.landed_local_master is False
    landed = admit_landed_true(
        ancestry_on_master=plane.landed_local_master,
        commits_ahead=state.commits_ahead,
        hub_master_has_work=state.merged_into_master or state.content_landed,
    )
    assert landed is True


def test_parse_land_disposition_unlanded_requires_sha() -> None:
    verb, reason, sha = parse_land_disposition("land_disposition: unlanded deadbeef")
    assert verb == "unlanded"
    assert reason is None
    assert sha == "deadbeef"
