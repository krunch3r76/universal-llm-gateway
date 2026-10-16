"""Patch-id landed discharge — friction:39445."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_branch_debt import (
    get_branch_debt,
    open_branch_debt,
)
from services.git_integration_worker.cursor_sdk_branch_discharge import (
    DischargeResult,
    LandProbe,
    discharge_landed,
    probe_landed,
)
from services.git_integration_worker.models.cursor_api import BranchDischargeRequest
from services.git_integration_worker.routes.cursor_sdk import cursor_branch_discharge


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
        content="alpha = 9\nbeta = 2\n",
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
    """Any reverted twin voids the match — line-superset cannot revive it."""
    branch = "cursor-sdk/lane-twin"
    tip = _branch_commit(repo, branch=branch, path="twin.py", content="t = 1\n")
    first = _equivalent_commit_on_master(repo, tip, message="first twin")
    _git("revert", "--no-edit", first, cwd=repo)
    _equivalent_commit_on_master(repo, tip, message="second twin")
    _land_on_master(repo, path="twin.py", content="t = 1\nextra = 2\n")

    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is False
    assert probe.patch_id_note is None


def test_voided_match_identical_blob_lands(repo: Path) -> None:
    """S4: a voided match still passes when master is byte-identical."""
    branch = "cursor-sdk/lane-twin-blob"
    tip = _branch_commit(repo, branch=branch, path="twin.py", content="t = 1\n")
    first = _equivalent_commit_on_master(repo, tip, message="first twin")
    _git("revert", "--no-edit", first, cwd=repo)
    _equivalent_commit_on_master(repo, tip, message="second twin")

    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is True
    assert probe.patch_id_note is None


def test_patch_id_match_not_ancestor_of_master_refuses(repo: Path) -> None:
    branch = "cursor-sdk/lane-side"
    tip = _branch_commit(repo, branch=branch, path="side.py", content="s = 1\n")
    _git("checkout", "-b", "side-ref", cwd=repo)
    _equivalent_commit_on_master(repo, tip)
    _git("checkout", "master", cwd=repo)
    _land_on_master(repo, path="side.py", content="other = 1\n")

    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is False
    assert probe.patch_id_note is None


def test_unmatched_commit_after_patch_id_match_refuses(repo: Path) -> None:
    """S1: a later unmatched edit of a matched file still needs the content probe."""
    branch = "cursor-sdk/lane-after-plus"
    tip = _branch_commit(repo, branch=branch, path="x.py", content="a = 1\n")
    _equivalent_commit_on_master(repo, tip)
    _git("checkout", branch, cwd=repo)
    (repo / "x.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    _git("add", "x.py", cwd=repo)
    _git("commit", "-m", "extra unique", cwd=repo)
    _git("checkout", "master", cwd=repo)

    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is False
    assert "x.py" in probe.differing_paths


def test_merge_commit_not_ancestor_refuses_patch_id_landed(repo: Path) -> None:
    """S6: a substantive merge's paths stay on the content probe."""
    branch = "cursor-sdk/lane-merge"
    tip = _branch_commit(repo, branch=branch, path="x.py", content="a = 1\n")
    _equivalent_commit_on_master(repo, tip)
    _git("checkout", "-b", "other", branch, cwd=repo)
    (repo / "x.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    _git("add", "x.py", cwd=repo)
    _git("commit", "-m", "other leg", cwd=repo)
    _git("checkout", branch, cwd=repo)
    _git("merge", "--no-ff", "--no-edit", "other", cwd=repo)
    _git("checkout", "master", cwd=repo)

    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is False
    assert "x.py" in probe.differing_paths


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


def test_whitespace_inside_token_is_not_an_empty_commit(repo: Path) -> None:
    """S7: a token edit is not skipped as whitespace-only."""
    branch = "cursor-sdk/lane-token"
    tip = _branch_commit(repo, branch=branch, path="s.py", content='label = "a b"\n')
    _equivalent_commit_on_master(repo, tip)
    _git("checkout", branch, cwd=repo)
    (repo / "s.py").write_text('label = "ab"\n', encoding="utf-8")
    _git("add", "s.py", cwd=repo)
    _git("commit", "-m", "token edit", cwd=repo)
    _git("checkout", "master", cwd=repo)

    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is False
    assert "s.py" in probe.differing_paths


def test_whole_file_deletion_inverse_refuses(repo: Path) -> None:
    """Inverse of a whole-file add is the later ``git rm``, with no revert trailer."""
    branch = "cursor-sdk/lane-rm"
    tip = _branch_commit(repo, branch=branch, path="f.py", content="z = 1\n")
    _equivalent_commit_on_master(repo, tip)
    _git("rm", "f.py", cwd=repo)
    _git("commit", "-m", "drop f without trailer", cwd=repo)

    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is False
    assert "f.py" in probe.missing_paths


def test_merge_empty_against_first_parent_does_not_refuse(repo: Path) -> None:
    """AC5: an ours-merge does not by itself refuse a patch-id land."""
    branch = "cursor-sdk/lane-ours"
    tip = _branch_commit(repo, branch=branch, path="k.py", content="k = 1\n")
    _equivalent_commit_on_master(repo, tip)
    _land_on_master(repo, path="k.py", content="k = 9\n")
    head = _git("rev-parse", branch, cwd=repo).stdout.strip()
    tree = _git("rev-parse", f"{branch}^{{tree}}", cwd=repo).stdout.strip()
    parent = _git("rev-parse", f"{branch}^", cwd=repo).stdout.strip()
    merged = subprocess.run(
        ["git", "commit-tree", tree, "-p", head, "-p", parent, "-m", "empty merge"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _git("update-ref", f"refs/heads/{branch}", merged, cwd=repo)

    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is True
    assert probe.patch_id_note is not None
    assert probe.patch_id_note.startswith("patch-id:")


def test_patch_id_note_when_any_path_skipped(repo: Path) -> None:
    branch = "cursor-sdk/lane-mixed"
    tip = _branch_commit(repo, branch=branch, path="kept.py", content="alpha = 1\n")
    _equivalent_commit_on_master(repo, tip)
    _land_on_master(repo, path="kept.py", content="alpha = 9\n")
    _git("checkout", branch, cwd=repo)
    (repo / "other.py").write_text("o = 1\n", encoding="utf-8")
    _git("add", "other.py", cwd=repo)
    _git("commit", "-m", "second file", cwd=repo)
    _git("checkout", "master", cwd=repo)
    _land_on_master(repo, path="other.py", content="o = 1\nz = 2\n")

    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is True
    assert probe.patch_id_note is not None
    assert probe.patch_id_note.startswith("patch-id:")
    assert tip in probe.patch_id_note


def test_patch_id_note_is_capped() -> None:
    from services.git_integration_worker.cursor_sdk_branch_discharge import (
        _PATCH_ID_NOTE_MAX,
        _format_patch_id_note,
    )

    pairs = [(f"{index:040x}", f"{index + 1:040x}") for index in range(40)]
    note = _format_patch_id_note(pairs)
    assert note is not None
    assert note.startswith("patch-id:")
    assert len(note) <= _PATCH_ID_NOTE_MAX
    assert note.endswith("…")


def test_patch_id_timeout_fails_closed(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(repo_arg: Path, *args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(
            cmd=["git", "-C", str(repo_arg), *args], timeout=60
        )

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_discharge._git",
        _boom,
    )
    probe = probe_landed(repo=repo, branch_name="cursor-sdk/lane-timeout")
    assert probe.landed is False
    assert "rev-parse timeout" in probe.describe()


def test_verbatim_token_whitespace_mismatch_refuses(repo: Path) -> None:
    """W1: cherry ``-`` from in-token whitespace is not a verbatim land."""
    branch = "cursor-sdk/lane-w1"
    _branch_commit(repo, branch=branch, path="w.py", content='l = "a b"\n')
    _land_on_master(repo, path="w.py", content='l = "ab"\n', message="token space")

    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is False
    assert "w.py" in probe.differing_paths
    assert probe.patch_id_note is None


def test_indentation_only_difference_stays_on_line_probe(repo: Path) -> None:
    """W2: indent-only cherry ``-`` uses the line probe and still lands."""
    branch = "cursor-sdk/lane-w2"
    _branch_commit(repo, branch=branch, path="w.py", content='    l = "ab"\n')
    _land_on_master(repo, path="w.py", content='l = "ab"\n', message="dedent")

    probe = probe_landed(repo=repo, branch_name=branch)
    assert probe.landed is True
    assert probe.patch_id_note is None


def test_branch_discharge_probe_block_includes_patch_id_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    note = "patch-id:abc=def"
    result = DischargeResult(
        discharged=True,
        branch="cursor-sdk/lane-note",
        verb="landed",
        probe=LandProbe(landed=True, patch_id_note=note),
    )

    def _discharge(**_kwargs: object) -> DischargeResult:
        return result

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_debt.get_branch_debt",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_debt.resolve_debt_source_repo",
        lambda *_args, **_kwargs: Path("/tmp"),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_discharge.discharge",
        _discharge,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_discharge.resolve_completing_dispatch_id",
        lambda *_args, **_kwargs: None,
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            worker_config=SimpleNamespace(
                source_repo=Path("/tmp"), dispatch_workspace=Path("/tmp")
            )
        )
    )
    request = Request({"type": "http", "app": app, "headers": []})
    payload = asyncio.run(
        cursor_branch_discharge(
            BranchDischargeRequest(branch="cursor-sdk/lane-note", verb="landed"),
            request,
        )
    )
    assert payload["probe"]["patch_id_note"] == note
