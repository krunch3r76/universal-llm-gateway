"""Regression tests for the diff-scoped green gate (thread 1142).

The full-tree `ruff check .` gate failed every land while master carried
pre-existing lint debt. The diff-scoped gate lints only the .py files the arc
introduces vs master. These tests pin that behavior and the P0 guard (an empty
.py change set must NOT fall back to a whole-tree lint).

The gate is exercised through ``integrate_op`` with the real gate script from
config, wrapped in ``bash -c`` (non-login) so PATH resolution of ruff/python is
hermetic — the production ``bash -lc`` form is a deployment concern proven on
the running stack, orthogonal to the diff-scoping logic under test.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from git_integrate.git_cas import diff_sha256
from git_integrate.integrate import integrate_op
from git_integrate.schema import RC_GATE_FAILED

from services.git_integration_worker.config import _DIFF_SCOPED_GATE_SCRIPT


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


def _gate() -> list[str]:
    return ["bash", "-c", _DIFF_SCOPED_GATE_SCRIPT]


def _source_repo_with_lint_debt(tmp_path: Path) -> Path:
    """Main repo whose master carries a pre-existing ruff violation."""
    repo = tmp_path / "source"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "t@e.com", cwd=repo)
    _git("config", "user.name", "T", cwd=repo)
    # F401: unused import — pre-existing master lint debt the gate must ignore.
    (repo / "legacy.py").write_text("import os\n")
    _git("add", "legacy.py", cwd=repo)
    _git("commit", "-m", "init with lint debt", cwd=repo)
    _git("checkout", "-b", "_integration_parked", cwd=repo)
    return repo


def _arc_worktree(
    tmp_path: Path, source_repo: Path, *, file_name: str, body: str
) -> Path:
    wt = tmp_path / "worktrees" / "test-arc"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-b", "arc/test-arc", str(wt), "master", cwd=source_repo)
    _git("config", "user.email", "t@e.com", cwd=wt)
    _git("config", "user.name", "T", cwd=wt)
    (wt / file_name).write_text(body)
    _git("add", file_name, cwd=wt)
    _git("commit", "-m", f"add {file_name}", cwd=wt)
    return wt


@pytest.fixture(autouse=True)
def _no_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("git_integrate.events.record", lambda *_a, **_k: None)


@pytest.mark.asyncio
async def test_clean_arc_passes_despite_master_lint_debt(tmp_path: Path) -> None:
    """The bug fix: a clean arc lands even though master has lint violations."""
    source_repo = _source_repo_with_lint_debt(tmp_path)
    wt = _arc_worktree(tmp_path, source_repo, file_name="feature.py", body="x = 1\n")

    out = await integrate_op(
        arc="test-arc",
        phase="phase-2",
        worktree_path=str(wt),
        approval="approved",
        expected_diff_sha256=diff_sha256(str(wt)),
        source_repo=str(source_repo),
        green_gate_cmd=_gate(),
    )

    assert out["status"] == "completed", out


@pytest.mark.asyncio
async def test_arc_with_lint_error_is_rejected(tmp_path: Path) -> None:
    """A violation in the arc's own new file still fails the gate."""
    source_repo = _source_repo_with_lint_debt(tmp_path)
    wt = _arc_worktree(
        tmp_path, source_repo, file_name="feature.py", body="import sys\n"
    )

    out = await integrate_op(
        arc="test-arc",
        phase="phase-2",
        worktree_path=str(wt),
        approval="approved",
        expected_diff_sha256=diff_sha256(str(wt)),
        source_repo=str(source_repo),
        green_gate_cmd=_gate(),
    )

    assert out["status"] == "rejected"
    assert out["reason_code"] == RC_GATE_FAILED
    # Output is bounded, not the full stdout.
    assert "gate_output_line_count" in out
    assert "gate_stdout" not in out


@pytest.mark.asyncio
async def test_empty_py_changeset_passes_without_fallback(tmp_path: Path) -> None:
    """P0 guard: arc touches no .py — gate passes, never lints the whole tree."""
    source_repo = _source_repo_with_lint_debt(tmp_path)
    wt = _arc_worktree(tmp_path, source_repo, file_name="notes.txt", body="hello\n")

    out = await integrate_op(
        arc="test-arc",
        phase="phase-2",
        worktree_path=str(wt),
        approval="approved",
        expected_diff_sha256=diff_sha256(str(wt)),
        source_repo=str(source_repo),
        green_gate_cmd=_gate(),
    )

    assert out["status"] == "completed", out


_RETENTION_TEST_REL = (
    "services/git_integration_worker/tests/test_conductor_lane_retention.py"
)
_RETENTION_NODE = "test_packet_reference_forbids_land_disposition_before_done"


def _init_repo(path: Path) -> None:
    path.mkdir()
    _git("init", "-b", "master", cwd=path)
    _git("config", "user.email", "t@e.com", cwd=path)
    _git("config", "user.name", "T", cwd=path)


def _run_gate(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _gate(), cwd=str(repo), capture_output=True, text=True, check=False
    )


def test_retention_clause_skipped_when_test_file_absent(tmp_path: Path) -> None:
    """A repo without the conductor subtree must not fail the gate with pytest exit 4."""
    repo = _source_repo_with_lint_debt(tmp_path)
    assert not (repo / _RETENTION_TEST_REL).is_file()

    proc = _run_gate(repo)
    combined = proc.stdout + proc.stderr

    assert proc.returncode == 0, combined
    assert "file or directory not found" not in combined
    assert f"[ -f {_RETENTION_TEST_REL} ]" in _DIFF_SCOPED_GATE_SCRIPT


def test_retention_clause_runs_when_test_file_exists(tmp_path: Path) -> None:
    """When the test file is present, the gate still executes that node."""
    repo = tmp_path / "source"
    _init_repo(repo)
    test_path = repo / _RETENTION_TEST_REL
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        "def test_packet_reference_forbids_land_disposition_before_done() -> None:\n"
        "    assert True\n",
        encoding="utf-8",
    )
    _git("add", _RETENTION_TEST_REL, cwd=repo)
    _git("commit", "-m", "add retention node", cwd=repo)

    proc = _run_gate(repo)
    combined = proc.stdout + proc.stderr

    assert proc.returncode == 0, combined
    assert "1 passed" in combined
    assert _RETENTION_NODE in _DIFF_SCOPED_GATE_SCRIPT


def test_retention_clause_failure_fails_gate_when_test_file_exists(
    tmp_path: Path,
) -> None:
    """A present file that fails still fails the gate; the guard does not skip it."""
    repo = tmp_path / "source"
    _init_repo(repo)
    test_path = repo / _RETENTION_TEST_REL
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        "def test_packet_reference_forbids_land_disposition_before_done() -> None:\n"
        "    assert False, 'retention-ran'\n",
        encoding="utf-8",
    )
    _git("add", _RETENTION_TEST_REL, cwd=repo)
    _git("commit", "-m", "add failing retention node", cwd=repo)

    proc = _run_gate(repo)
    combined = proc.stdout + proc.stderr

    assert proc.returncode == 1, combined
    assert "retention-ran" in combined
