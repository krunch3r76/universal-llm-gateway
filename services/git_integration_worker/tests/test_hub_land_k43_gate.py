"""Hub land is never refused for K43; post-land gap notices are advisory."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

import services.git_integration_worker.master_review_owed as mro
from services.git_integration_worker.cursor_dispatch_ledger import (
    CURSOR_SDK_DISPATCH_LEDGER_ENV,
)
from services.git_integration_worker.cursor_sdk_hub_land_scope import (
    clean_merge_onto_hub_master,
    ff_only_onto_hub_master,
    land_lane_branch_onto_hub_master,
)
from services.git_integration_worker.cursor_sdk_land_lease import (
    land_lease_holder,
    master_land_lease_key,
    release_land_lease,
    try_acquire_land_lease,
)
from services.git_integration_worker.master_review_owed import (
    bind_canonical_verdict,
    land_k43_review_status,
    record_land_exemption,
)
from services.git_integration_worker.record_land_exemption_cli import (
    main as exemption_cli,
)

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _noop_gap_poster(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mro, "_default_poster", lambda *_args: None)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _init_hub(tmp_path: Path) -> Path:
    repo = tmp_path / "hub"
    repo.mkdir()
    assert _git(repo, "init", "-b", "master").returncode == 0
    assert _git(repo, "config", "user.email", "t@example.com").returncode == 0
    assert _git(repo, "config", "user.name", "t").returncode == 0
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    assert _git(repo, "add", "README.md").returncode == 0
    assert _git(repo, "commit", "-m", "seed").returncode == 0
    _sync_review_window_floor(repo)
    return repo


def _sync_review_window_floor(repo: Path) -> None:
    floor = _master(repo).lower()
    conn, reason = mro._open_ledger(None)
    assert conn is not None, reason
    conn.execute(
        "UPDATE master_review_owed_state SET review_window_base = ?",
        (floor,),
    )
    conn.commit()
    conn.close()


def _commit_on_branch(repo: Path, branch: str, body: str) -> str:
    assert _git(repo, "checkout", "-b", branch).returncode == 0
    (repo / "lane.md").write_text(body, encoding="utf-8")
    assert _git(repo, "add", "lane.md").returncode == 0
    assert _git(repo, "commit", "-m", branch).returncode == 0
    tip = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert _git(repo, "checkout", "master").returncode == 0
    return tip


def _master(repo: Path) -> str:
    return _git(repo, "rev-parse", "refs/heads/master").stdout.strip()


def _parent(repo: Path, tip: str) -> str:
    return _git(repo, "rev-parse", f"{tip}^").stdout.strip()


def _approve(repo: Path, tip: str) -> None:
    base = _parent(repo, tip)
    body = f"VERDICT: APPROVE range={base}..{tip} head={tip}\n"
    result = bind_canonical_verdict(
        body,
        repo=repo,
        bus_thread="16416",
        bus_turn=1598,
        review_dispatch_id="k43-test",
    )
    assert result.status == "bound"
    assert result.verdict == "APPROVE"


def test_land_allows_when_tip_has_no_k43_row(tmp_path: Path) -> None:
    """Absent row: master moves; K43 is not a refuse_reason."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-no-k43"
    tip = _commit_on_branch(repo, branch, "lane\n")
    before = _master(repo)
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-no-k43"
    )
    assert result.landed is True
    assert result.refuse_reason is None
    assert _master(repo) == tip
    assert _master(repo) != before
    assert land_k43_review_status(tip) == "unreviewed"
    assert land_lease_holder(master_land_lease_key(repo)) is None
    key = master_land_lease_key(repo)
    assert try_acquire_land_lease(lease_key=key, holder_op_id="next-land")
    release_land_lease(lease_key=key, holder_op_id="next-land")


def test_land_allows_when_gap_row_has_no_verdict(tmp_path: Path) -> None:
    """A gap notice is not a bind. The land still proceeds."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-gap-only"
    tip = _commit_on_branch(repo, branch, "lane\n")
    conn, reason = mro._open_ledger(None)
    assert conn is not None, reason
    conn.execute(
        "INSERT INTO master_review_owed (tip_sha, base_sha, gap_posted_at) "
        "VALUES (?, ?, ?)",
        (tip.lower(), _parent(repo, tip).lower(), "2026-10-21T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-gap"
    )
    assert result.landed is True
    assert result.refuse_reason is None
    assert _master(repo) == tip


def test_land_allows_when_verdict_is_request_changes(tmp_path: Path) -> None:
    """REQUEST_CHANGES does not block hub land."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-request-changes"
    tip = _commit_on_branch(repo, branch, "lane\n")
    base = _parent(repo, tip)
    body = f"VERDICT: REQUEST_CHANGES range={base}..{tip} head={tip}\n"
    bound = bind_canonical_verdict(
        body,
        repo=repo,
        bus_thread="16416",
        bus_turn=1593,
        review_dispatch_id="k43-rc",
    )
    assert bound.status == "bound"
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-rc"
    )
    assert result.landed is True
    assert result.refuse_reason is None
    assert _master(repo) == tip


def test_land_allows_when_approve_is_for_a_different_tip(tmp_path: Path) -> None:
    """An APPROVE row for another sha does not block this tip's land."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-other-tip"
    tip = _commit_on_branch(repo, branch, "lane\n")
    (repo / "other.md").write_text("other\n", encoding="utf-8")
    assert _git(repo, "add", "other.md").returncode == 0
    assert _git(repo, "commit", "-m", "other").returncode == 0
    _approve(repo, _master(repo))
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-other"
    )
    assert result.landed is True
    assert result.refuse_reason is None
    assert _git(repo, "merge-base", "--is-ancestor", tip, "master").returncode == 0


def test_land_proceeds_when_k43_approve_bound(tmp_path: Path) -> None:
    """A bound APPROVE on the branch tip admits the fast-forward."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-approved"
    tip = _commit_on_branch(repo, branch, "lane\n")
    _approve(repo, tip)
    before = _master(repo)
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-approve"
    )
    assert result.landed is True
    assert result.refuse_reason is None
    assert _master(repo) == tip
    assert _master(repo) != before


def test_land_proceeds_when_exemption_recorded(tmp_path: Path) -> None:
    """A recorded exemption admits the land when no verdict is stored."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-exempt"
    tip = _commit_on_branch(repo, branch, "lane\n")
    status = record_land_exemption(
        repo,
        tip,
        base_sha=_parent(repo, tip),
        exemption_ref="16416#1598",
    )
    assert status == "recorded"
    before = _master(repo)
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-exempt"
    )
    assert result.landed is True
    assert result.refuse_reason is None
    assert _master(repo) == tip
    assert _master(repo) != before


def test_blank_exemption_ref_does_not_admit(tmp_path: Path) -> None:
    """Whitespace is not a recorded exemption. The land still proceeds."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-blank-exempt"
    tip = _commit_on_branch(repo, branch, "lane\n")
    assert (
        record_land_exemption(
            repo,
            tip,
            base_sha=_parent(repo, tip),
            exemption_ref="  ",
        )
        == "exemption_ref_empty"
    )
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-blank"
    )
    assert result.landed is True
    assert result.refuse_reason is None
    assert _master(repo) == tip


def test_land_allows_when_k43_ledger_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ledger connect failure warns, records override, and land proceeds."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-ledger-down"
    tip = _commit_on_branch(repo, branch, "lane\n")

    def _down(_path: Path) -> sqlite3.Connection:
        raise sqlite3.OperationalError("ledger down")

    monkeypatch.setattr(mro, "_connect", _down)
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-ledger-down"
    )
    assert result.landed is True
    assert result.refuse_reason is None
    assert _master(repo) == tip
    assert land_lease_holder(master_land_lease_key(repo)) is None


def test_legacy_ledger_without_exemption_column_still_allows_land(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy ledger schema migrates and does not block land."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-legacy-ledger"
    tip = _commit_on_branch(repo, branch, "lane\n")
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE master_review_owed (
            tip_sha TEXT PRIMARY KEY,
            base_sha TEXT NOT NULL,
            verdict TEXT,
            review_dispatch_id TEXT,
            bus_thread TEXT,
            bus_turn INTEGER,
            fixes_sha TEXT,
            gap_posted_at TEXT,
            notice_tag TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE master_review_owed_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            review_window_base TEXT NOT NULL,
            verdict_scan_before_id INTEGER NOT NULL,
            verdict_scan_after_turn INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "INSERT INTO master_review_owed_state "
        "(id, review_window_base, verdict_scan_before_id) VALUES (1, ?, 0)",
        (_master(repo).lower(),),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv(CURSOR_SDK_DISPATCH_LEDGER_ENV, str(db))
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    CursorDispatchLedger._instance = None
    _sync_review_window_floor(repo)
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-legacy"
    )
    assert result.landed is True
    assert result.refuse_reason is None
    assert _master(repo) == tip
    migrated = sqlite3.connect(db)
    columns = {
        str(row[1]) for row in migrated.execute("PRAGMA table_info(master_review_owed)")
    }
    migrated.close()
    assert "exemption_ref" in columns
    assert "exemption_issued_by" in columns
    assert land_k43_review_status(tip) == "unreviewed"


def _hold(repo: Path, holder: str) -> None:
    key = master_land_lease_key(repo)
    assert try_acquire_land_lease(lease_key=key, holder_op_id=holder)


def test_ff_only_allows_without_k43_and_merges_the_resolved_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No row: master moves. APPROVE status is advisory only."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-ff-sha"
    tip = _commit_on_branch(repo, branch, "lane\n")
    _hold(repo, "ff-sha")
    before = _master(repo)
    assert ff_only_onto_hub_master(repo, branch_name=branch) is True
    assert _master(repo) == tip
    assert _git(repo, "reset", "--hard", before).returncode == 0
    _sync_review_window_floor(repo)
    _approve(repo, tip)
    import services.git_integration_worker.cursor_sdk_hub_land_scope as scope

    real = scope._git_capture
    merges: list[tuple[str, ...]] = []

    def _spy(hub: Path, *args: str) -> subprocess.CompletedProcess[str]:
        if args and args[0] == "merge":
            merges.append(args)
        return real(hub, *args)

    monkeypatch.setattr(scope, "_git_capture", _spy)
    assert ff_only_onto_hub_master(repo, branch_name=branch) is True
    assert merges == [("merge", "--ff-only", tip)]
    assert _master(repo) == tip
    release_land_lease(lease_key=master_land_lease_key(repo), holder_op_id="ff-sha")


def test_clean_merge_allows_without_k43_and_merges_the_resolved_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behind master with no row still merges. APPROVE is not required."""
    repo = _init_hub(tmp_path)
    assert _git(repo, "checkout", "-b", "peer").returncode == 0
    (repo / "peer.md").write_text("peer\n", encoding="utf-8")
    assert _git(repo, "add", "peer.md").returncode == 0
    assert _git(repo, "commit", "-m", "peer").returncode == 0
    assert _git(repo, "checkout", "master").returncode == 0
    (repo / "hub.md").write_text("hub\n", encoding="utf-8")
    assert _git(repo, "add", "hub.md").returncode == 0
    assert _git(repo, "commit", "-m", "hub").returncode == 0
    tip = _git(repo, "rev-parse", "peer").stdout.strip()
    _hold(repo, "clean-sha")
    before = _master(repo)
    assert clean_merge_onto_hub_master(repo, branch_name="peer") is True
    assert _git(repo, "merge-base", "--is-ancestor", tip, "master").returncode == 0
    assert _git(repo, "reset", "--hard", before).returncode == 0
    _sync_review_window_floor(repo)
    _approve(repo, tip)
    import services.git_integration_worker.cursor_sdk_hub_land_scope as scope

    real = scope._git_capture
    merges: list[tuple[str, ...]] = []

    def _spy(hub: Path, *args: str) -> subprocess.CompletedProcess[str]:
        if args and args[0] == "merge":
            merges.append(args)
        return real(hub, *args)

    monkeypatch.setattr(scope, "_git_capture", _spy)
    assert clean_merge_onto_hub_master(repo, branch_name="peer") is True
    assert merges == [("merge", "--no-edit", tip)]
    assert _git(repo, "merge-base", "--is-ancestor", tip, "master").returncode == 0
    release_land_lease(lease_key=master_land_lease_key(repo), holder_op_id="clean-sha")


def test_exemption_cli_requires_citation_and_records_issuer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-citation and a blank issuer write nothing. A citation stores the issuer."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-cli"
    tip = _commit_on_branch(repo, branch, "lane\n")
    base = _parent(repo, tip)
    assert (
        exemption_cli(
            [
                "--repo",
                str(repo),
                "--tip",
                tip,
                "--base",
                base,
                "--ruling-turn",
                "note",
                "--issued-by",
                "cursor",
            ]
        )
        == 2
    )
    assert capsys.readouterr().out.strip() == "ruling_turn_not_citation"
    assert (
        exemption_cli(
            [
                "--repo",
                str(repo),
                "--tip",
                tip,
                "--base",
                base,
                "--ruling-turn",
                "16416#1631",
                "--issued-by",
                "  ",
            ]
        )
        == 2
    )
    assert capsys.readouterr().out.strip() == "issued_by_empty"
    assert land_k43_review_status(tip) == "unreviewed"
    assert (
        exemption_cli(
            [
                "--repo",
                str(repo),
                "--tip",
                tip,
                "--base",
                base,
                "--ruling-turn",
                "16416#1631",
                "--issued-by",
                "cursor",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.strip() == "recorded"
    from services.git_integration_worker.master_review_owed import _open_ledger

    conn, reason = _open_ledger(None)
    assert conn is not None, reason
    row = conn.execute(
        "SELECT exemption_ref, exemption_issued_by FROM master_review_owed WHERE tip_sha = ?",
        (tip.lower(),),
    ).fetchone()
    conn.close()
    assert row["exemption_ref"] == "16416#1631"
    assert row["exemption_issued_by"] == "cursor"
    assert land_k43_review_status(tip) == "exempt"


def test_silent_land_without_approve_lands_and_notices(tmp_path: Path) -> None:
    """T1: unbound tip and REQUEST_CHANGES both land via lane-branch and ff actuators."""
    repo = _init_hub(tmp_path)
    posts: list[str] = []
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        mro,
        "_default_poster",
        lambda subject, body, tag: posts.append(f"{tag}:{body}"),
    )
    try:
        branch = "cursor-sdk/lane-t1"
        tip = _commit_on_branch(repo, branch, "lane\n")
        result = land_lane_branch_onto_hub_master(
            repo, branch_name=branch, holder_op_id="d-t1"
        )
        assert result.landed is True
        assert result.refuse_reason is None
        conn, reason = mro._open_ledger(None)
        assert conn is not None, reason
        row = conn.execute(
            "SELECT gap_posted_at FROM master_review_owed WHERE tip_sha = ?",
            (tip.lower(),),
        ).fetchone()
        conn.close()
        assert row is not None and row["gap_posted_at"]
        assert any("Landed without a review yet" in body for body in posts)

        hub2_root = tmp_path / "hub2"
        hub2_root.mkdir(parents=True, exist_ok=True)
        repo2 = _init_hub(hub2_root)
        branch2 = "cursor-sdk/lane-t1-rc"
        tip2 = _commit_on_branch(repo2, branch2, "lane\n")
        base2 = _parent(repo2, tip2)
        bind_canonical_verdict(
            f"VERDICT: REQUEST_CHANGES range={base2}..{tip2} head={tip2}\n",
            repo=repo2,
            bus_thread="16416",
            bus_turn=1,
            review_dispatch_id="t1-rc",
        )
        _hold(repo2, "t1-rc")
        assert ff_only_onto_hub_master(repo2, branch_name=branch2) is True
        assert _master(repo2) == tip2
    finally:
        monkeypatch.undo()


def test_ledger_unavailable_warns_allows_records_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """T2: unreadable ledger lands once and appends one override spool line."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-t2"
    tip = _commit_on_branch(repo, branch, "lane\n")

    def _down(_path: Path) -> sqlite3.Connection:
        raise sqlite3.OperationalError("ledger down")

    monkeypatch.setattr(mro, "_connect", _down)
    with caplog.at_level("WARNING"):
        result = land_lane_branch_onto_hub_master(
            repo, branch_name=branch, holder_op_id="d-t2"
        )
    assert result.landed is True
    assert any(
        "ledger connect failed" in rec.message or "k43 land ledger" in rec.message
        for rec in caplog.records
    )
    spool = mro._k43_ledger_override_spool_path()
    assert spool.is_file()
    lines = [line for line in spool.read_text(encoding="utf-8").splitlines() if line]
    overrides = [
        line
        for line in lines
        if "ledger_unavailable_override" in line and tip.lower() in line
    ]
    assert len(overrides) == 1


def test_override_record_failure_does_not_block_land(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T3: override sink failure does not refuse land."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-t3"
    tip = _commit_on_branch(repo, branch, "lane\n")

    def _down(_path: Path) -> sqlite3.Connection:
        raise sqlite3.OperationalError("ledger down")

    monkeypatch.setattr(mro, "_connect", _down)

    def _boom(*_args: object, **_kwargs: object) -> bool:
        raise OSError("spool full")

    monkeypatch.setattr(mro, "record_k43_ledger_unavailable_override", _boom)
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-t3"
    )
    assert result.landed is True
    assert _master(repo) == tip


def test_no_route_refuses_on_k43(tmp_path: Path) -> None:
    """T4: no production module emits k43_* refuse reasons."""
    scope = Path(__file__).resolve().parents[1] / "cursor_sdk_hub_land_scope.py"
    text = scope.read_text(encoding="utf-8")
    assert "k43_unbound" not in text
    assert "k43_ledger_unavailable" not in text
    assert "refuse_reason=block" not in text.replace(" ", "")
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-t4"
    _commit_on_branch(repo, branch, "lane\n")
    lane = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-t4"
    )
    assert lane.refuse_reason is None or not str(lane.refuse_reason).startswith("k43_")
