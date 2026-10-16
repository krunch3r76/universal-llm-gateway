"""Patch-id landed discharge — friction:39445."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_branch_debt import (
    get_branch_debt,
    open_branch_debt,
)
from services.git_integration_worker.cursor_sdk_branch_discharge import (
    discharge_landed,
    probe_landed,
)


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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


def _branch_commit(
    repo: Path, *, branch: str, path: str, content: str, message: str = "work"
) -> str:
    _git("checkout", "-b", branch, cwd=repo)
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git("add", path, cwd=repo)
    _git("commit", "-m", message, cwd=repo)
    tip = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", branch],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _git("checkout", "master", cwd=repo)
    return tip


def _land_on_master(repo: Path, *, path: str, content: str, message: str = "land") -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git("add", path, cwd=repo)
    _git("commit", "-m", message, cwd=repo)


def _equivalent_commit_on_master(repo: Path, sha: str, *, message: str = "on master") -> str:
    """Same patch-id as ``sha`` but a distinct master commit."""
    _git("cherry-pick", "-n", sha, cwd=repo)
    _git("commit", "-m", message, cwd=repo)
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_specimen_shape_forward_edit_after_patch_id_lands(repo: Path) -> None:
    branch = "cursor-sdk/lane-specimen"
    tip = _branch_commit(
        repo,
        branch=branch,
        path="pkg/feature.py",
        content="alpha = 1\nbeta = 2\n",
    )
    _equivalent_commit_on_master(repo, tip)
    _land_on_master(
        repo,
        path="pkg/feature.py",
        content="alpha = 1\nbeta = 2\ngamma = 99\n",
        message="forward edit on master",
    )
    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is True
    assert probe.patch_id_note is not None
    assert probe.patch_id_note.startswith("patch-id:")
    assert tip in probe.patch_id_note

    open_branch_debt(
        branch_name=branch,
        thread_id="17356",
        dispatch_id="d-specimen",
        tip_sha=tip,
        files=["pkg/feature.py"],
    )
    result = discharge_landed(repo=repo, branch_name=branch)
    assert result.discharged is True
    assert result.verb == "landed"
    debt = get_branch_debt(branch_name=branch)
    assert debt is not None
    assert debt.discharge_verb == "landed"
    assert debt.discharge_note is not None
    assert debt.discharge_note.startswith("patch-id:")
    assert tip in debt.discharge_note


def test_patch_id_match_later_reverted_on_master_refuses(repo: Path) -> None:
    branch = "cursor-sdk/lane-revert-trailer"
    tip = _branch_commit(repo, branch=branch, path="a.py", content="x = 1\n")
    _equivalent_commit_on_master(repo, tip)
    _git("revert", "--no-edit", "HEAD", cwd=repo)

    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is False
    result = discharge_landed(repo=repo, branch_name=branch)
    assert result.discharged is False

    # Revert undid a deletion: branch removes a line master restored — no superset pass.
    branch2 = "cursor-sdk/lane-revert-deletion"
    (repo / "drop.py").write_text("keep = 1\nremove = 2\n", encoding="utf-8")
    _git("add", "drop.py", cwd=repo)
    _git("commit", "-m", "base file", cwd=repo)
    _git("checkout", "-b", branch2, cwd=repo)
    (repo / "drop.py").write_text("keep = 1\n", encoding="utf-8")
    _git("add", "drop.py", cwd=repo)
    _git("commit", "-m", "delete line on branch", cwd=repo)
    del_tip = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _git("checkout", "master", cwd=repo)
    _equivalent_commit_on_master(repo, del_tip)
    _git("revert", "--no-edit", "HEAD", cwd=repo)
    probe2 = probe_landed(repo=repo, branch_name=branch2)
    assert probe2.landed is False
    assert "drop.py" in probe2.differing_paths


def test_patch_id_match_inverse_patch_on_master_refuses(repo: Path) -> None:
    """§5 — inverse patch-id on master voids the match like a revert trailer."""
    (repo / "inv.py").write_text("a\n", encoding="utf-8")
    _git("add", "inv.py", cwd=repo)
    _git("commit", "-m", "base inv", cwd=repo)
    branch = "cursor-sdk/lane-inverse"
    tip = _branch_commit(repo, branch=branch, path="inv.py", content="a\nb\n")
    _equivalent_commit_on_master(repo, tip)
    # Manual inverse (no revert trailer): drop the ``b`` line master had picked up.
    (repo / "inv.py").write_text("a\n", encoding="utf-8")
    _git("add", "inv.py", cwd=repo)
    _git("commit", "-m", "manual inverse without trailer", cwd=repo)
    # Forward edit so branch lines are a subset of master — superset would lie.
    (repo / "inv.py").write_text("a\nb\nc\n", encoding="utf-8")
    _git("add", "inv.py", cwd=repo)
    _git("commit", "-m", "forward superset on master", cwd=repo)

    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is False
    assert probe.patch_id_note is None


def test_patch_id_match_any_reverted_twin_refuses(repo: Path) -> None:
    """Several master commits share patch-id — any reverted twin voids the match."""
    branch = "cursor-sdk/lane-twin"
    tip = _branch_commit(repo, branch=branch, path="twin.py", content="t = 1\n")
    first = _equivalent_commit_on_master(repo, tip, message="first twin")
    _git("revert", "--no-edit", first, cwd=repo)
    _equivalent_commit_on_master(repo, tip, message="second twin")

    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is False


def test_patch_id_match_not_ancestor_of_master_refuses(repo: Path) -> None:
    branch = "cursor-sdk/lane-side"
    tip = _branch_commit(repo, branch=branch, path="side.py", content="s = 1\n")
    _git("checkout", "-b", "side-ref", cwd=repo)
    _equivalent_commit_on_master(repo, tip)
    _git("checkout", "master", cwd=repo)

    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is False


def test_unmatched_commit_after_patch_id_match_refuses(repo: Path) -> None:
    branch = "cursor-sdk/lane-after-plus"
    tip = _branch_commit(repo, branch=branch, path="first.py", content="a = 1\n")
    _equivalent_commit_on_master(repo, tip)
    _git("checkout", branch, cwd=repo)
    (repo / "second.py").write_text("b = 2\n", encoding="utf-8")
    _git("add", "second.py", cwd=repo)
    _git("commit", "-m", "extra unique", cwd=repo)
    _git("checkout", "master", cwd=repo)

    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is False


def test_merge_commit_not_ancestor_refuses_patch_id_landed(repo: Path) -> None:
    branch = "cursor-sdk/lane-merge"
    _branch_commit(repo, branch=branch, path="m.py", content="m = 1\n")
    _git("checkout", "master", cwd=repo)
    _git("checkout", "-b", "other", cwd=repo)
    (repo / "other.py").write_text("o = 1\n", encoding="utf-8")
    _git("add", "other.py", cwd=repo)
    _git("commit", "-m", "other leg", cwd=repo)
    _git("checkout", branch, cwd=repo)
    _git("merge", "--no-edit", "other", cwd=repo)
    _git("checkout", "master", cwd=repo)

    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is False


def test_empty_and_whitespace_commits_are_not_patch_id_evidence(repo: Path) -> None:
    branch = "cursor-sdk/lane-empty"
    _git("checkout", "-b", branch, cwd=repo)
    _git("commit", "--allow-empty", "-m", "empty", cwd=repo)
    (repo / "ws.py").write_text("   \n\t\n", encoding="utf-8")
    _git("add", "ws.py", cwd=repo)
    _git("commit", "-m", "whitespace only", cwd=repo)
    _git("checkout", "master", cwd=repo)

    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is False

    # Name-only diff empty on master still lands (existing rule).
    branch_only_empty = "cursor-sdk/lane-empty-only"
    _git("checkout", "-b", branch_only_empty, cwd=repo)
    _git("commit", "--allow-empty", "-m", "only empty", cwd=repo)
    _git("checkout", "master", cwd=repo)
    probe_empty = probe_landed(repo=repo, branch_name=branch_only_empty)
    assert probe_empty.landed is True
