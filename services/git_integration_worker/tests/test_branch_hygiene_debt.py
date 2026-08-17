"""Branch hygiene: the grade is carried, not swept.

Covers the whole arc — obligation stated at admit, debt opened when a lane goes
silent, discharge as the cheap clean exit, a content probe that refuses an
unsupported landed claim, hygiene surfaced at the next dispatch decision,
escalation that announces instead of deleting, and worktree reconcile.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.model_manager.ui.controller.busy_work_summary import (
    format_active_work_summary,
)
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
)
from services.git_integration_worker.cursor_sdk_branch_archive import (
    archive_branch,
    archive_tag_name,
)
from services.git_integration_worker.cursor_sdk_branch_debt import (
    discharge_branch_debt,
    ensure_debt_schema,
    get_branch_debt,
    lane_hygiene_snapshot,
    list_open_debts,
    open_branch_debt,
)
from services.git_integration_worker.cursor_sdk_branch_debt_escalation import (
    debt_admit_refusal,
    escalate_aged_debts,
)
from services.git_integration_worker.cursor_sdk_branch_debt_tags import (
    LAND_REQUIRED_TAG,
    add_land_required_tag,
    remove_land_required_tag,
)
from services.git_integration_worker.cursor_sdk_branch_discharge import (
    discharge_discard,
    discharge_landed,
    probe_landed,
)
from services.git_integration_worker.cursor_sdk_branch_terminal import (
    parse_land_disposition,
    settle_lane_branch,
)
from services.git_integration_worker.cursor_sdk_packet import resolve_prompt_preamble
from services.git_integration_worker.cursor_sdk_worktree_prune import (
    _delete_orphan_branch,
)
from services.git_integration_worker.cursor_sdk_worktree_reconcile import (
    list_git_worktrees,
    reconcile_unregistered_worktrees,
)


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


def _branches(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
        check=True,
    )
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def _tags(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "tag", "--list"],
        capture_output=True,
        text=True,
        check=True,
    )
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-b", "master", cwd=root)
    _git("config", "user.email", "test@example.com", cwd=root)
    _git("config", "user.name", "test", cwd=root)
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=root)
    _git("commit", "-m", "seed", cwd=root)
    return root


def _branch_with_change(repo: Path, *, branch: str, path: str, content: str) -> str:
    _git("checkout", "-b", branch, cwd=repo)
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git("add", path, cwd=repo)
    _git("commit", "-m", f"work on {branch}", cwd=repo)
    tip = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", branch],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _git("checkout", "master", cwd=repo)
    return tip


def _land_on_master(repo: Path, *, path: str, content: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git("add", path, cwd=repo)
    _git("commit", "-m", f"lead lands {path}", cwd=repo)


def _age_debt(branch: str, *, days: float) -> None:
    stamp = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with _connect() as conn:
        ensure_debt_schema(conn)
        conn.execute(
            "UPDATE cursor_sdk_branch_debts SET opened_at=? WHERE branch_name=?",
            (stamp, branch),
        )


# --- obligation stated at the front -----------------------------------------


def test_lane_b_preamble_states_the_branch_contract() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="implement",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        lane_branch="cursor-sdk/lane-7229",
    )
    assert "LANE-B BRANCH CONTRACT" in preamble
    assert "cursor-sdk/lane-7229" in preamble
    assert "land_disposition: landed" in preamble
    assert "land_disposition: discard" in preamble


def test_lane_a_gets_no_branch_contract() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="implement",
        prompt_preamble=None,
        inferred_contract=None,
        lane=None,
    )
    assert "LANE-B BRANCH CONTRACT" not in preamble


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("land_disposition: landed", ("landed", None)),
        (
            "land_disposition: `discard`\nland_reason: superseded",
            ("discard", "superseded"),
        ),
        ("LAND_DISPOSITION:  Landed  ", ("landed", None)),
        ("nothing declared here", (None, None)),
    ],
)
def test_parse_land_disposition(
    text: str, expected: tuple[str | None, str | None]
) -> None:
    assert parse_land_disposition(text) == expected


# --- terminal: declared discharges, silence opens debt -----------------------


def test_undeclared_terminal_opens_attributed_debt(repo: Path) -> None:
    tip = _branch_with_change(
        repo, branch="cursor-sdk/lane-7229", path="a.py", content="x = 1\n"
    )
    settlement = settle_lane_branch(
        source_repo=repo,
        branch_name="cursor-sdk/lane-7229",
        thread_id="7229",
        dispatch_id="d-1",
        closeout_text="did the work, no disposition line",
        commits_ahead=1,
        landed=False,
        head_sha=tip,
        files=["a.py"],
    )
    assert settlement.outcome == "debt_opened"
    debt = get_branch_debt(branch_name="cursor-sdk/lane-7229")
    assert debt is not None
    assert debt.open
    assert debt.thread_id == "7229"
    assert debt.dispatch_id == "d-1"
    assert debt.tip_sha == tip
    # Evidence preserved: the branch is not touched when a debt opens.
    assert "cursor-sdk/lane-7229" in _branches(repo)


def test_declared_landed_discharges_when_master_has_the_content(repo: Path) -> None:
    _branch_with_change(
        repo, branch="cursor-sdk/lane-7165", path="b.py", content="y = 2\n"
    )
    _land_on_master(repo, path="b.py", content="y = 2\nz = 3\n")

    settlement = settle_lane_branch(
        source_repo=repo,
        branch_name="cursor-sdk/lane-7165",
        thread_id="7165",
        dispatch_id="d-2",
        closeout_text="land_disposition: landed",
        commits_ahead=1,
        landed=False,
    )
    assert settlement.outcome == "discharged"
    assert settlement.verb == "landed"
    assert "cursor-sdk/lane-7165" not in _branches(repo)
    assert settlement.archive_tag in _tags(repo)
    assert list_open_debts() == []


def test_no_commits_means_nothing_owed(repo: Path) -> None:
    settlement = settle_lane_branch(
        source_repo=repo,
        branch_name="cursor-sdk/lane-7000",
        thread_id="7000",
        dispatch_id="d-3",
        closeout_text="read-only pass",
        commits_ahead=0,
        landed=None,
    )
    assert settlement.outcome == "nothing_owed"
    assert list_open_debts() == []


# --- the probe refuses claims the tree does not support ----------------------


def test_content_probe_refuses_a_false_landed_claim(repo: Path) -> None:
    _branch_with_change(
        repo, branch="cursor-sdk/lane-7208", path="c.py", content="unique = True\n"
    )
    result = discharge_landed(repo=repo, branch_name="cursor-sdk/lane-7208")
    assert not result.discharged
    assert result.probe is not None
    assert "c.py" in result.probe.missing_paths
    assert "not landed" in (result.refused_reason or "")
    assert "cursor-sdk/lane-7208" in _branches(repo)


def test_false_landed_claim_at_terminal_opens_debt(repo: Path) -> None:
    _branch_with_change(
        repo, branch="cursor-sdk/lane-7214", path="d.py", content="unique = True\n"
    )
    settlement = settle_lane_branch(
        source_repo=repo,
        branch_name="cursor-sdk/lane-7214",
        thread_id="7214",
        dispatch_id="d-4",
        closeout_text="land_disposition: landed",
        commits_ahead=1,
        landed=False,
    )
    assert settlement.outcome == "debt_opened"
    debt = get_branch_debt(branch_name="cursor-sdk/lane-7214")
    assert debt is not None and debt.open
    assert "cursor-sdk/lane-7214" in _branches(repo)


def test_probe_accepts_a_superset_on_master(repo: Path) -> None:
    _branch_with_change(
        repo, branch="cursor-sdk/lane-7170", path="e.py", content="a = 1\n"
    )
    _land_on_master(repo, path="e.py", content="a = 1\nb = 2\nc = 3\n")
    probe = probe_landed(repo=repo, branch_name="cursor-sdk/lane-7170")
    assert probe.landed
    assert probe.describe() == "landed"


def test_probe_reports_ref_missing_not_no_merge_base(repo: Path) -> None:
    probe = probe_landed(repo=repo, branch_name="cursor-sdk/lane-missing")
    assert not probe.landed
    assert probe.differing_paths == ["cursor-sdk/lane-missing (ref missing)"]
    assert "(no merge-base)" not in probe.describe()


def test_checked_out_blocks_orphan_branch_delete(repo: Path, tmp_path: Path) -> None:
    tip = _branch_with_change(
        repo, branch="cursor-sdk/lane-7112", path="held.py", content="held = 1\n"
    )
    _land_on_master(repo, path="held.py", content="held = 1\n")
    tree = tmp_path / "held-tree"
    _git("worktree", "add", str(tree), "cursor-sdk/lane-7112", cwd=repo)

    deleted = _delete_orphan_branch(
        repo=repo,
        branch_name="cursor-sdk/lane-7112",
        reason="ancestry_merged",
        dispatch_id="d-held",
        tip_sha=tip,
    )
    assert not deleted
    assert "cursor-sdk/lane-7112" in _branches(repo)


def test_open_debt_blocks_orphan_branch_delete(repo: Path) -> None:
    tip = _branch_with_change(
        repo, branch="cursor-sdk/lane-7413", path="race.py", content="landed = 1\n"
    )
    _land_on_master(repo, path="race.py", content="landed = 1\n")
    open_branch_debt(
        branch_name="cursor-sdk/lane-7413",
        thread_id="7413",
        dispatch_id="d-race",
        tip_sha=tip,
    )

    deleted = _delete_orphan_branch(
        repo=repo,
        branch_name="cursor-sdk/lane-7413",
        reason="prune_terminal",
        dispatch_id="d-race",
        tip_sha=tip,
    )
    assert not deleted
    assert "cursor-sdk/lane-7413" in _branches(repo)
    debt = get_branch_debt(branch_name="cursor-sdk/lane-7413")
    assert debt is not None and debt.open


def test_discharge_landed_idempotent_when_ref_already_retired(repo: Path) -> None:
    tip = _branch_with_change(
        repo, branch="cursor-sdk/lane-7413", path="idem.py", content="done = 1\n"
    )
    _land_on_master(repo, path="idem.py", content="done = 1\n")
    tag = archive_branch(repo=repo, branch_name="cursor-sdk/lane-7413")
    assert tag is not None
    open_branch_debt(
        branch_name="cursor-sdk/lane-7413",
        thread_id="7413",
        tip_sha=tip,
    )
    _git("branch", "-D", "cursor-sdk/lane-7413", cwd=repo)
    discharge_branch_debt(
        branch_name="cursor-sdk/lane-7413",
        verb="landed",
        note="prune_terminal",
    )

    result = discharge_landed(repo=repo, branch_name="cursor-sdk/lane-7413")
    assert result.discharged
    assert result.archive_tag == tag
    assert result.tip_sha == tip
    assert "cursor-sdk/lane-7413" not in _branches(repo)


# --- discard: archived, reasoned, deleted ------------------------------------


def test_discard_archives_then_deletes(repo: Path) -> None:
    tip = _branch_with_change(
        repo, branch="cursor-sdk/lane-7083", path="f.py", content="scrap = 1\n"
    )
    open_branch_debt(branch_name="cursor-sdk/lane-7083", thread_id="7083")
    result = discharge_discard(
        repo=repo,
        branch_name="cursor-sdk/lane-7083",
        reason="superseded by a later refactor",
    )
    assert result.discharged
    assert result.archive_tag == archive_tag_name("cursor-sdk/lane-7083", tip)
    assert result.archive_tag in _tags(repo)
    assert "cursor-sdk/lane-7083" not in _branches(repo)
    debt = get_branch_debt(branch_name="cursor-sdk/lane-7083")
    assert debt is not None
    assert not debt.open
    assert debt.discharge_verb == "discard"
    assert debt.discharge_note == "superseded by a later refactor"


def test_discard_requires_a_reason(repo: Path) -> None:
    _branch_with_change(
        repo, branch="cursor-sdk/lane-7099", path="g.py", content="scrap = 1\n"
    )
    result = discharge_discard(
        repo=repo, branch_name="cursor-sdk/lane-7099", reason="  "
    )
    assert not result.discharged
    assert "requires a reason" in (result.refused_reason or "")
    assert "cursor-sdk/lane-7099" in _branches(repo)


def test_discharge_refuses_a_checked_out_branch(repo: Path, tmp_path: Path) -> None:
    _branch_with_change(
        repo, branch="cursor-sdk/lane-7111", path="h.py", content="held = 1\n"
    )
    tree = tmp_path / "held-tree"
    _git("worktree", "add", str(tree), "cursor-sdk/lane-7111", cwd=repo)
    result = discharge_discard(
        repo=repo, branch_name="cursor-sdk/lane-7111", reason="abandon"
    )
    assert not result.discharged
    assert "checked out" in (result.refused_reason or "")
    assert "cursor-sdk/lane-7111" in _branches(repo)


# --- hygiene standing at the decision point ----------------------------------


def test_lane_hygiene_names_the_owing_lane() -> None:
    open_branch_debt(branch_name="cursor-sdk/lane-7229", thread_id="7229")
    open_branch_debt(branch_name="cursor-sdk/lane-7165", thread_id="7229")
    open_branch_debt(branch_name="cursor-sdk/lane-7208", thread_id="7208")

    snapshot = lane_hygiene_snapshot()
    assert snapshot["open_debts"] == 3
    assert snapshot["debts_by_lane"] == {"7229": 2, "7208": 1}
    assert snapshot["oldest_debt_age_s"] is not None


def test_busy_summary_renders_branch_debt() -> None:
    summary = format_active_work_summary(
        {
            "active_count": 0,
            "lane_b": {
                "aged_orphans": [],
                "lane_hygiene": {
                    "open_debts": 2,
                    "oldest_debt_age_s": 172800,
                    "debts_by_lane": {"7229": 2},
                },
            },
        }
    )
    assert "branch_debt=2" in summary
    assert "owing_lane=7229(2)" in summary


def test_busy_summary_silent_when_nothing_owed() -> None:
    summary = format_active_work_summary(
        {
            "active_count": 0,
            "lane_b": {"aged_orphans": [], "lane_hygiene": {"open_debts": 0}},
        }
    )
    assert "branch_debt" not in summary


# --- escalation announces; it never deletes ----------------------------------


def test_aged_debt_escalates_without_deleting(repo: Path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_BUS_TOKEN", raising=False)
    _branch_with_change(
        repo, branch="cursor-sdk/lane-7229", path="i.py", content="k = 1\n"
    )
    open_branch_debt(branch_name="cursor-sdk/lane-7229", thread_id="7229")
    _age_debt("cursor-sdk/lane-7229", days=3)

    assert escalate_aged_debts() == 1
    # The branch survives: escalation raises visibility, it does not sweep.
    assert "cursor-sdk/lane-7229" in _branches(repo)
    debt = get_branch_debt(branch_name="cursor-sdk/lane-7229")
    assert debt is not None and debt.open and debt.escalated_at is not None
    # Escalation is once per debt, not once per sweep.
    assert escalate_aged_debts() == 0


def test_fresh_debt_does_not_escalate() -> None:
    open_branch_debt(branch_name="cursor-sdk/lane-7300", thread_id="7300")
    assert escalate_aged_debts() == 0


def test_admit_refusal_only_past_the_hard_horizon(monkeypatch) -> None:
    monkeypatch.setenv("CURSOR_SDK_BRANCH_DEBT_REFUSAL_HORIZON_S", str(14 * 86400))
    open_branch_debt(branch_name="cursor-sdk/lane-7229", thread_id="7229")

    _age_debt("cursor-sdk/lane-7229", days=3)
    assert debt_admit_refusal("7229") is None

    _age_debt("cursor-sdk/lane-7229", days=20)
    refusal = debt_admit_refusal("7229")
    assert refusal is not None
    assert "cursor-sdk/lane-7229" in refusal
    assert "branch-discharge" in refusal


def test_admit_refusal_is_lane_scoped(monkeypatch) -> None:
    monkeypatch.setenv("CURSOR_SDK_BRANCH_DEBT_REFUSAL_HORIZON_S", str(14 * 86400))
    open_branch_debt(branch_name="cursor-sdk/lane-7229", thread_id="7229")
    _age_debt("cursor-sdk/lane-7229", days=20)
    assert debt_admit_refusal("7208") is None


# --- worktree reconcile ------------------------------------------------------


def test_unregistered_clean_worktree_is_reconciled(repo: Path, tmp_path: Path) -> None:
    root = tmp_path / "worktrees"
    root.mkdir()
    _branch_with_change(
        repo, branch="cursor-sdk/lane-9001", path="j.py", content="m = 1\n"
    )
    tree = root / "stray"
    _git("worktree", "add", str(tree), "cursor-sdk/lane-9001", cwd=repo)
    assert any(w.path == tree.resolve() for w in list_git_worktrees(source_repo=repo))

    reconciled, surfaced = reconcile_unregistered_worktrees(
        source_repo=repo, worktree_root=root
    )
    assert (reconciled, surfaced) == (1, 0)
    assert not tree.exists()
    assert archive_tag_name(
        "cursor-sdk/lane-9001",
        subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "cursor-sdk/lane-9001"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
    ) in _tags(repo)


def test_unregistered_dirty_worktree_is_surfaced_not_touched(
    repo: Path, tmp_path: Path
) -> None:
    root = tmp_path / "worktrees"
    root.mkdir()
    _branch_with_change(
        repo, branch="cursor-sdk/lane-9002", path="k.py", content="n = 1\n"
    )
    tree = root / "dirty"
    _git("worktree", "add", str(tree), "cursor-sdk/lane-9002", cwd=repo)
    (tree / "uncommitted.py").write_text("wip = True\n", encoding="utf-8")

    reconciled, surfaced = reconcile_unregistered_worktrees(
        source_repo=repo, worktree_root=root
    )
    assert (reconciled, surfaced) == (0, 1)
    assert tree.exists()
    assert (tree / "uncommitted.py").is_file()
    debt = get_branch_debt(branch_name="cursor-sdk/lane-9002")
    assert debt is not None and debt.open


def test_active_worktree_is_left_alone(repo: Path, tmp_path: Path) -> None:
    root = tmp_path / "worktrees"
    root.mkdir()
    _branch_with_change(
        repo, branch="cursor-sdk/lane-9003", path="l.py", content="p = 1\n"
    )
    tree = root / "live"
    _git("worktree", "add", str(tree), "cursor-sdk/lane-9003", cwd=repo)

    reconciled, surfaced = reconcile_unregistered_worktrees(
        source_repo=repo,
        worktree_root=root,
        active={str(tree.resolve())},
    )
    assert (reconciled, surfaced) == (0, 0)
    assert tree.exists()


def test_reconcile_leaves_arc_worktree_under_root(repo: Path, tmp_path: Path) -> None:
    root = tmp_path / "worktrees"
    root.mkdir()
    _git("checkout", "-b", "arc/still-live", cwd=repo)
    (repo / "arc.md").write_text("keep\n", encoding="utf-8")
    _git("add", "arc.md", cwd=repo)
    _git("commit", "-m", "arc", cwd=repo)
    _git("checkout", "master", cwd=repo)
    tree = root / "arc-still-live"
    _git("worktree", "add", str(tree), "arc/still-live", cwd=repo)

    reconciled, surfaced = reconcile_unregistered_worktrees(
        source_repo=repo, worktree_root=root
    )
    assert (reconciled, surfaced) == (0, 0)
    assert tree.exists()
    assert "arc/still-live" in _branches(repo)


def test_settle_open_debt_adds_land_required_tag(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    def _fake_add(*, thread_id: str | None) -> bool:
        calls.append({"thread_id": thread_id})
        return True

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_terminal.add_land_required_tag",
        _fake_add,
    )
    tip = _branch_with_change(
        repo, branch="cursor-sdk/lane-7421", path="x.py", content="x\n"
    )
    settlement = settle_lane_branch(
        source_repo=repo,
        branch_name="cursor-sdk/lane-7421",
        thread_id="7421",
        dispatch_id="d-7421",
        closeout_text="status: partial",
        commits_ahead=1,
        landed=False,
        head_sha=tip,
        files=["x.py"],
    )
    assert settlement.outcome == "debt_opened"
    assert calls == [{"thread_id": "7421"}]


def test_discharge_removes_land_required_tag(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    def _fake_remove(*, thread_id: str | None) -> bool:
        calls.append({"thread_id": thread_id})
        return True

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_discharge.remove_land_required_tag",
        _fake_remove,
    )
    tip = _branch_with_change(
        repo, branch="cursor-sdk/lane-7422", path="y.py", content="y\n"
    )
    open_branch_debt(
        branch_name="cursor-sdk/lane-7422",
        thread_id="7422",
        dispatch_id="d-7422",
        tip_sha=tip,
        files=["y.py"],
    )
    (repo / "y.py").write_text("y\n", encoding="utf-8")
    _git("add", "y.py", cwd=repo)
    _git("commit", "-m", "land y", cwd=repo)
    result = discharge_landed(repo=repo, branch_name="cursor-sdk/lane-7422")
    assert result.discharged is True
    assert calls == [{"thread_id": "7422"}]


def test_land_required_tag_helpers_no_thread() -> None:
    assert add_land_required_tag(thread_id=None) is False
    assert remove_land_required_tag(thread_id="") is False
    assert LAND_REQUIRED_TAG == "land_required"


# --- test isolation guard ----------------------------------------------------


def test_ledger_path_refuses_the_live_gateway_dir(monkeypatch) -> None:
    from services.git_integration_worker.cursor_dispatch_ledger import _ledger_path

    monkeypatch.setenv("DATA_DIR", str(Path.home() / ".gateway"))
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_branch_hygiene_debt")
    with pytest.raises(RuntimeError, match="refusing to open the live dispatch ledger"):
        _ledger_path()
