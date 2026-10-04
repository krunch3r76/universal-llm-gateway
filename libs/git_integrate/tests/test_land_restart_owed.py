"""land_op stamps restart_owed from the advanced range (friction a:37771)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from implement_admission.restart_owed import restart_owed_for_range

from git_integrate.git_cas import advance_master_cas
from git_integrate.land import land_op
from git_integrate.schema import RC_CAS_EXHAUSTED, RC_GATE_FAILED


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _ref_sha(repo: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ref],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _receipt_dir(repo: Path) -> Path:
    return repo / "tmp" / "reviews" / "land-receipts"


@pytest.mark.asyncio
async def test_land_envelope_matches_range_helper(
    source_repo: Path, tmp_path: Path
) -> None:
    wt = tmp_path / "worktrees" / "owed-arc"
    wt.parent.mkdir(parents=True)
    _git("worktree", "add", "-b", "arc/owed-arc", str(wt), "master", cwd=source_repo)
    _git("config", "user.email", "test@example.com", cwd=wt)
    _git("config", "user.name", "Test", cwd=wt)
    loader = wt / "services/universal-stargate/systems/pipeline/registry/loader.py"
    loader.parent.mkdir(parents=True)
    loader.write_text("# loader\n", encoding="utf-8")
    vocab = wt / "libs/capability_tree/vocabulary.py"
    vocab.parent.mkdir(parents=True)
    vocab.write_text("# vocab\n", encoding="utf-8")
    before = _ref_sha(source_repo, "refs/heads/master")
    from git_integrate.git_cas import land_fingerprint

    fingerprint = land_fingerprint(str(wt))
    out = await land_op(
        arc="owed-arc",
        phase="phase-3",
        worktree_path=str(wt),
        approval="approved",
        expected_diff_sha256=fingerprint,
        commit_message="land stargate paths",
        source_repo=str(source_repo),
        green_gate_cmd=["true"],
        remove_worktree=False,
    )
    assert out["status"] == "completed", out
    assert out["master_before_sha"] == before
    assert out["master_after_sha"] == out["master_sha"]
    expected = restart_owed_for_range(
        source_repo, out["master_before_sha"], out["master_after_sha"]
    )
    assert out["restart_owed_block"] == expected
    assert "restart_owed: stargate" in out["restart_owed_block"]
    receipt = _receipt_dir(source_repo) / f"{out['master_after_sha']}.md"
    text = receipt.read_text(encoding="utf-8")
    assert out["restart_owed_block"] in text
    assert "land_path: advance_master_cas" in text
    assert f"before: {before}" in text


@pytest.mark.asyncio
async def test_gate_refusal_writes_no_receipt(
    source_repo: Path, arc_worktree: Path
) -> None:
    from git_integrate.git_cas import diff_sha256

    sha = diff_sha256(str(arc_worktree))
    out = await land_op(
        arc="test-arc",
        phase="phase-3",
        worktree_path=str(arc_worktree),
        approval="approved",
        expected_diff_sha256=sha,
        source_repo=str(source_repo),
        green_gate_cmd=["false"],
        remove_worktree=False,
    )
    assert out["status"] == "rejected"
    assert out["reason_code"] == RC_GATE_FAILED
    assert not _receipt_dir(source_repo).exists()


@pytest.mark.asyncio
async def test_cas_conflict_writes_no_receipt(
    source_repo: Path, arc_worktree: Path
) -> None:
    result = await advance_master_cas(
        str(source_repo),
        str(arc_worktree),
        expected="0" * 40,
    )
    assert result.non_ff
    assert result.restart_owed_block == ""
    assert not _receipt_dir(source_repo).exists()


@pytest.mark.asyncio
async def test_cas_exhausted_land_writes_no_receipt(
    source_repo: Path,
    arc_worktree: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from git_integrate.git_cas import diff_sha256
    from git_integrate.integrate import integrate_op
    from git_integrate.schema import CasResult

    async def _always_non_ff(src: str, wtp: str, *, expected: str) -> CasResult:
        return CasResult(non_ff=True)

    monkeypatch.setattr("git_integrate.ops_common.advance_master_cas", _always_non_ff)
    sha = diff_sha256(str(arc_worktree))
    out = await integrate_op(
        arc="test-arc",
        phase="phase-3",
        worktree_path=str(arc_worktree),
        approval="approved",
        expected_diff_sha256=sha,
        source_repo=str(source_repo),
        green_gate_cmd=["true"],
        max_attempts=1,
    )
    assert out["status"] == "rejected"
    assert out["reason_code"] == RC_CAS_EXHAUSTED
    assert not _receipt_dir(source_repo).exists()


@pytest.mark.asyncio
async def test_diff_failure_land_still_completes(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt = tmp_path / "worktrees" / "unreadable-arc"
    wt.parent.mkdir(parents=True)
    _git(
        "worktree",
        "add",
        "-b",
        "arc/unreadable-arc",
        str(wt),
        "master",
        cwd=source_repo,
    )
    _git("config", "user.email", "test@example.com", cwd=wt)
    _git("config", "user.name", "Test", cwd=wt)
    (wt / "libs/capability_tree").mkdir(parents=True)
    (wt / "libs/capability_tree/vocabulary.py").write_text(
        "# vocab\n", encoding="utf-8"
    )

    def _boom(*_args: object, **_kwargs: object) -> list[str]:
        raise OSError("unreadable repo")

    monkeypatch.setattr("implement_admission.restart_owed._diff_name_only", _boom)
    from git_integrate.git_cas import land_fingerprint

    fingerprint = land_fingerprint(str(wt))
    out = await land_op(
        arc="unreadable-arc",
        phase="phase-3",
        worktree_path=str(wt),
        approval="approved",
        expected_diff_sha256=fingerprint,
        commit_message="land despite diff failure",
        source_repo=str(source_repo),
        green_gate_cmd=["true"],
        remove_worktree=False,
    )
    assert out["status"] == "completed", out
    assert out["restart_owed_block"] == "restart_owed: unavailable (OSError)"
    receipt = _receipt_dir(source_repo) / f"{out['master_after_sha']}.md"
    assert "restart_owed: unavailable (OSError)" in receipt.read_text(encoding="utf-8")
