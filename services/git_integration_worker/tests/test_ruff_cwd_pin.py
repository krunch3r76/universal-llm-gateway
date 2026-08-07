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
from services.git_integration_worker.cursor_sdk_closeout import run_touched_files_lint

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
    verification, note = run_touched_files_lint(
        _REPO,
        ChangeSet(created=(_CLEAN_REL,), modified=(), deleted=()),
    )
    assert note is None
    assert verification.exit_code == 0
    assert captured["kwargs"].get("cwd") == str(_REPO)
