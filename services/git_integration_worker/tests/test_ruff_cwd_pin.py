"""AC1 — ruff measurement must pin cwd to the owning repo root.

Old ``run_touched_files_lint`` / ``quality._run_ruff`` omitted ``cwd=``. An
orphan copy of an in-tree-clean file (no ``pyproject.toml`` ancestor) emits
phantom I001; the same bytes under the repo root are clean. After the pin,
both call sites pass ``cwd`` equal to the repo base — a test that only asserts
``ruff ran`` does not discharge this.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet
from services.git_integration_worker.cursor_sdk_closeout import (
    _ruff_toolchain_identity,
    run_giw_subtree_f821_lint,
    run_touched_files_lint,
)

_REPO = Path(__file__).resolve().parents[3]
_CLEAN_REL = (
    "libs/cortex_store/dispatch_ops/test_assertion_update_dropped_key_warnings.py"
)


def test_orphan_copy_emits_phantom_i001_in_tree_clean() -> None:
    """The case that started this: outside project root ≠ inside."""
    src = _REPO / _CLEAN_REL
    assert src.is_file()
    in_tree = subprocess.run(
        ["ruff", "check", str(src), "--select", "I"],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert in_tree.returncode == 0, in_tree.stdout + in_tree.stderr

    with tempfile.TemporaryDirectory() as tmp:
        orphan = Path(tmp) / "orphan_probe.py"
        shutil.copyfile(src, orphan)
        outside = subprocess.run(
            ["ruff", "check", str(orphan), "--select", "I"],
            cwd="/tmp",
            capture_output=True,
            text=True,
            check=False,
        )
    assert outside.returncode != 0, "orphan copy must fail without project config"
    assert "I001" in (outside.stdout + outside.stderr)


def test_run_touched_files_lint_pins_cwd_to_source_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old code omits cwd= — this assertion fails before the pin."""
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = b""
        proc.stderr = b""
        return proc

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.subprocess.run",
        fake_run,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout._ruff_toolchain_identity",
        lambda: ("/venv/bin/ruff", "0.15.6"),
    )
    verification, note = run_touched_files_lint(
        _REPO,
        ChangeSet(created=(_CLEAN_REL,), modified=(), deleted=()),
    )
    assert note is None
    assert verification.exit_code == 0
    assert captured["kwargs"].get("cwd") == str(_REPO)
    assert verification.stdout is None
    assert verification.stderr is None
    assert verification.executable == "/venv/bin/ruff"
    assert verification.tool_version == "0.15.6"


def test_run_touched_files_lint_retains_streams_on_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-zero ruff must keep stdout/stderr on the verification row."""

    def fake_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        del cmd, kwargs
        proc = MagicMock()
        proc.returncode = 1
        proc.stdout = b"file.py:1:1: F401 unused import\n"
        proc.stderr = b""
        return proc

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.subprocess.run",
        fake_run,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout._ruff_toolchain_identity",
        lambda: ("/venv/bin/ruff", "0.15.6"),
    )
    verification, note = run_touched_files_lint(
        _REPO,
        ChangeSet(created=(_CLEAN_REL,), modified=(), deleted=()),
    )
    assert note is None
    assert verification.exit_code == 1
    assert verification.stdout == "file.py:1:1: F401 unused import\n"
    assert verification.stderr == ""
    assert verification.output_truncated is False
    assert verification.executable == "/venv/bin/ruff"
    assert verification.tool_version == "0.15.6"


def test_run_touched_files_lint_truncates_oversized_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Streams longer than the retain budget are cut and flagged."""
    from services.git_integration_worker.cursor_sdk_closeout import (
        _LINT_OUTPUT_RETAIN_CHARS,
    )

    oversized = ("x" * (_LINT_OUTPUT_RETAIN_CHARS + 50)).encode()

    def fake_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        del cmd, kwargs
        proc = MagicMock()
        proc.returncode = 1
        proc.stdout = b""
        proc.stderr = oversized
        return proc

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.subprocess.run",
        fake_run,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout._ruff_toolchain_identity",
        lambda: ("/venv/bin/ruff", "0.15.6"),
    )
    verification, note = run_touched_files_lint(
        _REPO,
        ChangeSet(created=(_CLEAN_REL,), modified=(), deleted=()),
    )
    assert note is None
    assert verification.exit_code == 1
    assert verification.output_truncated is True
    assert verification.stderr is not None
    assert verification.stderr.endswith("\n...[truncated]")
    assert len(verification.stderr) == _LINT_OUTPUT_RETAIN_CHARS + len(
        "\n...[truncated]"
    )


def test_ruff_toolchain_identity_names_path_binary_not_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stamp the binary PATH will run — importlib.metadata would hide a shadow."""
    fake = tmp_path / "ruff"
    fake.write_text("#!/bin/sh\necho 'ruff 0.12.2'\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    executable, version = _ruff_toolchain_identity()
    assert executable == str(fake)
    assert version == "0.12.2"


def test_run_giw_subtree_f821_lint_passes_on_clean_subtree() -> None:
    """GIW subtree F821 gate passes when the package has no undefined names."""
    verification, note = run_giw_subtree_f821_lint(_REPO)
    assert note is None
    assert verification.exit_code == 0
    assert verification.command == "ruff check --select F821 services/git_integration_worker/"


def test_run_giw_subtree_f821_lint_pins_cwd_to_source_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subprocess cwd must be the owning repo root (same invariant as touched-files lint)."""
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        captured["cwd"] = kwargs.get("cwd")
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = b""
        proc.stderr = b""
        return proc

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.subprocess.run",
        fake_run,
    )
    verification, note = run_giw_subtree_f821_lint(_REPO)
    assert note is None
    assert verification.exit_code == 0
    assert captured["cwd"] == str(_REPO)
