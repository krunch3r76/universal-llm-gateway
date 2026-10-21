"""Hub land refuses unless the branch tip has an APPROVE K43 or an exemption.

Specimen class: a land with no ``master_review_owed`` row. The historical
merge ``a2dec646`` is not rewritten. Ledger unreadability fails closed.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CURSOR_SDK_DISPATCH_LEDGER_ENV,
)
from services.git_integration_worker.cursor_sdk_hub_land_scope import (
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
    land_k43_block_reason,
    record_land_exemption,
)

pytestmark = pytest.mark.offline


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
    return repo


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


def test_land_refuses_when_tip_has_no_k43_row(tmp_path: Path) -> None:
    """Absent row: the specimen class. Master does not move and the lease drops."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-no-k43"
    _commit_on_branch(repo, branch, "lane\n")
    before = _master(repo)
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-no-k43"
    )
    assert result.landed is False
    assert result.refuse_reason == "k43_unbound"
    assert result.before_sha == before
    assert result.after_sha == before
    assert _master(repo) == before
    assert _git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode != 0
    assert land_lease_holder(master_land_lease_key(repo)) is None
    key = master_land_lease_key(repo)
    assert try_acquire_land_lease(lease_key=key, holder_op_id="next-land")
    release_land_lease(lease_key=key, holder_op_id="next-land")


def test_land_refuses_when_gap_row_has_no_verdict(tmp_path: Path) -> None:
    """A gap notice is not a bind. The land still refuses."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-gap-only"
    tip = _commit_on_branch(repo, branch, "lane\n")
    from services.git_integration_worker.master_review_owed import _open_ledger

    conn, reason = _open_ledger(None)
    assert conn is not None, reason
    conn.execute(
        "INSERT INTO master_review_owed (tip_sha, base_sha, gap_posted_at) "
        "VALUES (?, ?, ?)",
        (tip.lower(), _parent(repo, tip).lower(), "2026-10-21T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    before = _master(repo)
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-gap"
    )
    assert result.landed is False
    assert result.refuse_reason == "k43_unbound"
    assert _master(repo) == before


def test_land_refuses_when_verdict_is_request_changes(tmp_path: Path) -> None:
    """REQUEST_CHANGES is bound for review and still does not admit a land."""
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
    before = _master(repo)
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-rc"
    )
    assert result.landed is False
    assert result.refuse_reason == "k43_unbound"
    assert _master(repo) == before


def test_land_refuses_when_approve_is_for_a_different_tip(tmp_path: Path) -> None:
    """An APPROVE row for another sha does not admit this tip."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-other-tip"
    _commit_on_branch(repo, branch, "lane\n")
    (repo / "other.md").write_text("other\n", encoding="utf-8")
    assert _git(repo, "add", "other.md").returncode == 0
    assert _git(repo, "commit", "-m", "other").returncode == 0
    _approve(repo, _master(repo))
    before = _master(repo)
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-other"
    )
    assert result.landed is False
    assert result.refuse_reason == "k43_unbound"
    assert _master(repo) == before


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
    """Whitespace is not a recorded exemption. The land stays refused."""
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
    before = _master(repo)
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-blank"
    )
    assert result.landed is False
    assert result.refuse_reason == "k43_unbound"
    assert _master(repo) == before


def test_land_refuses_when_k43_ledger_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ledger connect failure fails closed and leaves hub master on the same sha."""
    repo = _init_hub(tmp_path)
    branch = "cursor-sdk/lane-ledger-down"
    _commit_on_branch(repo, branch, "lane\n")
    import services.git_integration_worker.master_review_owed as mro

    def _down(_path: Path) -> sqlite3.Connection:
        raise sqlite3.OperationalError("ledger down")

    monkeypatch.setattr(mro, "_connect", _down)
    before = _master(repo)
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-ledger-down"
    )
    assert result.landed is False
    assert result.refuse_reason == "k43_ledger_unavailable"
    assert _master(repo) == before
    assert land_lease_holder(master_land_lease_key(repo)) is None


def test_legacy_ledger_without_exemption_column_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ledger created before ``exemption_ref`` gains the column and still refuses the land."""
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
    conn.commit()
    conn.close()
    monkeypatch.setenv(CURSOR_SDK_DISPATCH_LEDGER_ENV, str(db))
    before = _master(repo)
    result = land_lane_branch_onto_hub_master(
        repo, branch_name=branch, holder_op_id="d-legacy"
    )
    assert result.landed is False
    assert result.refuse_reason == "k43_unbound"
    assert _master(repo) == before
    migrated = sqlite3.connect(db)
    columns = {
        str(row[1]) for row in migrated.execute("PRAGMA table_info(master_review_owed)")
    }
    migrated.close()
    assert "exemption_ref" in columns
    assert land_k43_block_reason(tip) == "k43_unbound"
