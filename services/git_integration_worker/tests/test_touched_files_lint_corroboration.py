"""Touched-files lint three-state corroboration (friction-37009)."""

from __future__ import annotations

import inspect
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from implement_admission.closeout_models import observed_process_verification
from implement_admission.spec import WorkOutcome

from services.git_integration_worker.cursor_home import dispatch_git_identity
from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    resolve_work_outcome,
)
from services.git_integration_worker.cursor_sdk_closeout.delivery_assembly import (
    orchestration,
)
from services.git_integration_worker.cursor_sdk_closeout.lint_verification import (
    run_touched_files_lint,
)
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_closeout_lint_unestablished,
)
from services.git_integration_worker.cursor_sdk_exit_interpretation import (
    interpret_exit,
    row_blocks_all_pass,
    row_is_failed_check,
)
from services.git_integration_worker.cursor_sdk_git_head import (
    git_diff_paths_between,
    range_python_corroboration,
)
from services.git_integration_worker.cursor_sdk_repo_precedence import (
    _lane_exclusive_paths,
    resolve_repo_change_set,
)

pytestmark = pytest.mark.offline
_EMPTY = ChangeSet(created=(), modified=(), deleted=())
_DEVIATION = "verification:lint_set_unestablished"
_LINT = "services.git_integration_worker.cursor_sdk_closeout.lint_verification"
_GIT = "services.git_integration_worker.cursor_sdk_git_head"
_KEYS = {"dispatch_id", "thread_id", "authority", "projection", "recovery"}


def _assert_state3(row, deviation: str | None) -> None:
    assert row.exit_code is None and row.wrapper_exit_code is None
    assert row.exit_code_register == "unobserved"
    assert row.basis == "lint_set_unestablished"
    assert row.command == "ruff check (lint set unestablished)"
    assert str(row.invocation_id).startswith("lint-unestablished:")
    assert deviation == _DEVIATION


def _forbid_subprocess(monkeypatch: pytest.MonkeyPatch, module: str) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError(f"subprocess.run called in {module}")

    monkeypatch.setattr(f"{module}.subprocess.run", boom)


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.stdout.strip()


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    _git(path, "config", "user.email", "peer@example.com")
    _git(path, "config", "user.name", "peer")


def _commit(repo: Path, rel: str, content: str, *, delete: bool = False) -> str:
    target = repo / rel
    if delete:
        target.unlink()
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "peer",
        "GIT_AUTHOR_EMAIL": "peer@example.com",
        "GIT_COMMITTER_NAME": "peer",
        "GIT_COMMITTER_EMAIL": "peer@example.com",
    }
    _git(repo, "add", "-A", env=env)
    _git(repo, "commit", "-m", rel, env=env)
    return _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("corroboration", ["resolved_has_python", "unresolved"])
def test_ac1_ac10_empty_attributed_py_is_unobserved(
    monkeypatch: pytest.MonkeyPatch, corroboration: str
) -> None:
    _forbid_subprocess(monkeypatch, _LINT)
    row, deviation = run_touched_files_lint(
        Path("."), _EMPTY, range_corroboration=corroboration
    )
    _assert_state3(row, deviation)


def test_ac2_resolved_no_python_keeps_honest_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_subprocess(monkeypatch, _LINT)
    row, deviation = run_touched_files_lint(
        Path("."), _EMPTY, range_corroboration="resolved_no_python"
    )
    assert (row.exit_code, row.exit_code_register, row.basis, deviation) == (
        0,
        "derived",
        "lint_skipped_no_python",
        None,
    )
    assert row.command == "ruff check (no python files touched)"


def test_ac3_state1_ruff_argv_is_attributed_paths_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], **_kwargs: object) -> MagicMock:
        captured["cmd"] = list(cmd)
        proc = MagicMock()
        proc.returncode, proc.stdout, proc.stderr = 0, b"", b""
        return proc

    monkeypatch.setattr(f"{_LINT}.subprocess.run", fake_run)
    monkeypatch.setattr(
        f"{_LINT}._ruff_toolchain_identity", lambda: ("/venv/bin/ruff", "0.15.6")
    )
    repo, rel = Path("/repo"), "pkg/touched.py"
    row, deviation = run_touched_files_lint(
        repo,
        ChangeSet(created=(rel,), modified=("notes.md",), deleted=("gone.py",)),
        range_corroboration="unresolved",
    )
    assert deviation is None and row.exit_code_register == "observed"
    assert captured["cmd"] == ["ruff", "check", str(repo / rel)]


def test_ac4_missing_or_failed_diff_is_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_subprocess(monkeypatch, _GIT)
    for admit, close in ((None, "abc"), ("", "abc"), ("abc", None)):
        got = range_python_corroboration(
            Path("."), admit_head=admit, closeout_head=close
        )
        assert got == "unresolved"
    monkeypatch.undo()
    holder: dict[str, BaseException] = {}

    def boom(*_a: object, **_k: object) -> None:
        raise holder["exc"]

    monkeypatch.setattr(f"{_GIT}.subprocess.run", boom)
    failures = (
        subprocess.CalledProcessError(1, ["git", "diff"]),
        subprocess.TimeoutExpired(["git", "diff"], 10),
        OSError("diff failed"),
    )
    for exc in failures:
        holder["exc"] = exc
        got = range_python_corroboration(
            Path("."), admit_head="aaaa", closeout_head="bbbb"
        )
        assert got == "unresolved"


def test_ac5_state3_blocks_shipped_as_unverified(tmp_path: Path) -> None:
    row, deviation = run_touched_files_lint(
        tmp_path, _EMPTY, range_corroboration="resolved_has_python"
    )
    _assert_state3(row, deviation)
    assert interpret_exit(row) == "unobserved"
    assert row_is_failed_check(row) is False and row_blocks_all_pass(row) is True
    sibling = observed_process_verification(
        command="pytest -q", exit_code=0, basis="subprocess.run.returncode"
    )
    outcome = resolve_work_outcome(
        degraded_reason=None,
        verification=[row, sibling],
        manifest=None,
        source_repo=tmp_path,
        cortex_root=tmp_path,
    )
    assert outcome == WorkOutcome.UNVERIFIED
    assert outcome not in (WorkOutcome.NOT_SHIPPED, WorkOutcome.CHECKS_FAILED)


def test_ac6_event_payload_and_state3_only_emit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_events._emit", captured.append
    )
    for projection in ("resolved_has_python", "unresolved"):
        emit_sdk_closeout_lint_unestablished(
            dispatch_id="d-ac6",
            thread_id="t-ac6",
            authority="repo_change_set",
            projection=projection,
            recovery="unobserved",
        )
        event = captured[-1]
        assert event.signal == "frontier.sdk.closeout.lintunestablished"
        assert event.role == "observation" and event.scope == "node"
        assert set(event.payload) == _KEYS and event.payload["projection"] == projection
        assert "path" not in event.payload
    body = inspect.getsource(orchestration._assemble_closeout_delivery)
    gate = 'if "verification:lint_set_unestablished" in baseline_deviations:'
    assert gate in body
    assert body.index(gate) < body.index("emit_sdk_closeout_lint_unestablished(")


def test_ac7_attribution_signatures_unchanged() -> None:
    diff_sig = inspect.signature(git_diff_paths_between)
    assert list(diff_sig.parameters) == ["source_repo", "admit_head", "closeout_head"]
    assert diff_sig.parameters["admit_head"].kind is inspect.Parameter.KEYWORD_ONLY
    assert (
        git_diff_paths_between(Path("."), admit_head=None, closeout_head=None)
        == frozenset()
    )
    want = (
        "manifest git_change_set source_repo mount_root baseline files_expected "
        "current_porcelain admit_head closeout_head dispatch_id"
    )
    assert list(inspect.signature(resolve_repo_change_set).parameters) == want.split()
    assert len(resolve_repo_change_set(manifest=None, git_change_set=_EMPTY)) == 4
    assert "with_head_sha_fallback" not in inspect.getsource(_lane_exclusive_paths)


def test_ac9_unattributed_lane_py_is_state3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch_id = "d-not-author"
    _init_repo(tmp_path)
    admit = _commit(tmp_path, "README.md", "# base\n")
    same = range_python_corroboration(tmp_path, admit_head=admit, closeout_head=admit)
    assert same == "resolved_no_python"
    rel = "lane_only.py"
    closeout = _commit(tmp_path, rel, "value = 1\n")
    author, committer = _git(tmp_path, "log", "-1", "--format=%ae%x00%ce").split("\x00")
    _name, email = dispatch_git_identity(dispatch_id)
    assert author != email and committer != email
    change_set, _extra, _div, ambient = resolve_repo_change_set(
        manifest=None,
        git_change_set=_EMPTY,
        source_repo=tmp_path,
        admit_head=admit,
        closeout_head=closeout,
        dispatch_id=dispatch_id,
    )
    attributed = [
        path
        for path in (*change_set.created, *change_set.modified)
        if path.endswith(".py")
    ]
    assert attributed == [] and any(entry.path == rel for entry in ambient)
    corroboration = range_python_corroboration(
        tmp_path, admit_head=admit, closeout_head=closeout
    )
    assert corroboration == "resolved_has_python"
    deleted = _commit(tmp_path, rel, "", delete=True)
    deleted_range = range_python_corroboration(
        tmp_path, admit_head=closeout, closeout_head=deleted
    )
    assert deleted_range == "resolved_has_python"
    _forbid_subprocess(monkeypatch, _LINT)
    row, deviation = run_touched_files_lint(
        tmp_path, change_set, range_corroboration=corroboration
    )
    _assert_state3(row, deviation)
