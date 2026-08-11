"""Arc 6655 — blocking F821 land gate (before terminal commit and git_land).

Historical proof pins the gate against commit ``f1151d3d`` (undefined
``load_config`` on nested relay path) and ``7eca1bc7`` (clean GIW subtree).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from git_integrate.git_cas import diff_sha256
from git_integrate.integrate import integrate_op
from git_integrate.schema import RC_GATE_FAILED

from services.git_integration_worker.config import (
    GIW_SUBTREE_F821_REL,
    _DIFF_SCOPED_GATE_SCRIPT,
)
from services.git_integration_worker.cursor_sdk_lane_b_commit import salvage_commit
from services.git_integration_worker.giw_f821_gate import (
    giw_subtree_f821_command,
    run_giw_subtree_f821_check,
)

_REPO = Path(__file__).resolve().parents[3]
_BAD_COMMIT = "f1151d3d46fd432d3d6b41180c94b99f99742670"
_GOOD_COMMIT = "7eca1bc7c13c595879ffd0d962b4c5fabdbafd68"


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _gate_cmd() -> list[str]:
    return ["bash", "-c", _DIFF_SCOPED_GATE_SCRIPT]


def _run_at_commit(repo: Path, sha: str) -> subprocess.CompletedProcess[str]:
    """Run the GIW F821 gate command with ``repo`` checked out at *sha*."""
    _git("checkout", "--force", sha, cwd=repo)
    return subprocess.run(
        giw_subtree_f821_command(),
        shell=True,
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="module")
def bare_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Minimal clone of this repo for historical checkout without mutating workspace."""
    base = tmp_path_factory.mktemp("giw_f821_hist")
    dest = base / "mirror"
    _git("clone", "--no-local", str(_REPO), str(dest), cwd=_REPO)
    return dest


def test_legitimate_f821_survey_no_runtime_intentional_cases() -> None:
    """Only vulture whitelist uses ``noqa: F821``; TYPE_CHECKING blocks are clean."""
    whitelist = _REPO / "vulture_whitelist.py"
    proc = subprocess.run(
        [
            "rg",
            "-l",
            "# noqa: F821",
            "--glob",
            "*.py",
            "--glob",
            "!**/tests/**",
            str(_REPO),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    repo_root = _REPO.resolve()
    noqa_files = [
        line
        for line in (proc.stdout or "").splitlines()
        if line and Path(line).resolve().is_relative_to(repo_root)
    ]
    assert noqa_files == [str(whitelist.resolve())]


def test_historical_gate_refuses_f1151d3d(bare_repo: Path) -> None:
    """Gate must refuse the commit that shipped undefined ``load_config``."""
    proc = _run_at_commit(bare_repo, _BAD_COMMIT)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "load_config" in proc.stdout + proc.stderr
    assert "F821" in proc.stdout + proc.stderr


def test_historical_gate_passes_7eca1bc7(bare_repo: Path) -> None:
    """Gate passes at the fix commit that restored the import."""
    proc = _run_at_commit(bare_repo, _GOOD_COMMIT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip() in ("", "All checks passed!")


def test_green_gate_script_includes_giw_subtree_f821() -> None:
    assert GIW_SUBTREE_F821_REL in _DIFF_SCOPED_GATE_SCRIPT
    assert "--select F821" in _DIFF_SCOPED_GATE_SCRIPT


def test_salvage_commit_refuses_when_giw_subtree_has_f821(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "wt"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "t@e.com", cwd=repo)
    _git("config", "user.name", "T", cwd=repo)
    giw = repo / "services" / "git_integration_worker"
    giw.mkdir(parents=True)
    (giw / "broken.py").write_text("x = undefined_name\n", encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)
    (giw / "broken.py").write_text("y = another_missing\n", encoding="utf-8")

    result = salvage_commit(repo, message="should refuse")
    assert result.committed is False
    assert result.refused is True
    assert result.error is not None
    assert "giw_f821_gate" in result.error


def test_run_giw_subtree_f821_check_passes_on_clean_repo() -> None:
    check = run_giw_subtree_f821_check(_REPO)
    assert check.passed
    assert check.command == giw_subtree_f821_command()


@pytest.mark.asyncio
async def test_land_green_gate_rejects_giw_f821_despite_clean_arc_diff(
    tmp_path: Path,
) -> None:
    """GIW F821 runs even when the arc diff touches no .py files."""
    source_repo = tmp_path / "source"
    source_repo.mkdir()
    _git("init", "-b", "master", cwd=source_repo)
    _git("config", "user.email", "t@e.com", cwd=source_repo)
    _git("config", "user.name", "T", cwd=source_repo)
    giw = source_repo / "services" / "git_integration_worker"
    giw.mkdir(parents=True)
    (giw / "bad.py").write_text("z = not_defined\n", encoding="utf-8")
    _git("add", ".", cwd=source_repo)
    _git("commit", "-m", "master with f821", cwd=source_repo)
    _git("checkout", "-b", "_integration_parked", cwd=source_repo)

    wt = tmp_path / "worktrees" / "arc"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-b", "arc/test-arc", str(wt), "master", cwd=source_repo)
    _git("config", "user.email", "t@e.com", cwd=wt)
    _git("config", "user.name", "T", cwd=wt)
    (wt / "notes.txt").write_text("no python\n", encoding="utf-8")
    _git("add", "notes.txt", cwd=wt)
    _git("commit", "-m", "txt only arc", cwd=wt)

    out = await integrate_op(
        arc="test-arc",
        phase="phase-2",
        worktree_path=str(wt),
        approval="approved",
        expected_diff_sha256=diff_sha256(str(wt)),
        source_repo=str(source_repo),
        green_gate_cmd=_gate_cmd(),
    )

    assert out["status"] == "rejected"
    assert out["reason_code"] == RC_GATE_FAILED
