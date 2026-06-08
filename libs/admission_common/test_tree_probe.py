"""Unit tests for admission_common.tree_probe."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from admission_common.tree_probe import probe_working_tree


def test_probe_working_tree_clean(tmp_path):
    with patch("admission_common.tree_probe.subprocess.run") as run:
        run.return_value = MagicMock(stdout="", returncode=0)
        status, dirty = probe_working_tree(str(tmp_path))
    assert status == ""
    assert dirty is False
    run.assert_called_once()


def test_probe_working_tree_dirty(tmp_path):
    with patch("admission_common.tree_probe.subprocess.run") as run:
        run.return_value = MagicMock(stdout=" M file.py\n", returncode=0)
        status, dirty = probe_working_tree(str(tmp_path))
    assert "file.py" in status
    assert dirty is True


def test_probe_working_tree_git_failure_fail_safe(tmp_path):
    with patch(
        "admission_common.tree_probe.subprocess.run",
        side_effect=subprocess.CalledProcessError(128, "git"),
    ):
        status, dirty = probe_working_tree(str(tmp_path))
    assert status == ""
    assert dirty is True


def test_probe_working_tree_timeout_fail_safe(tmp_path):
    with patch(
        "admission_common.tree_probe.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5),
    ):
        status, dirty = probe_working_tree(str(tmp_path))
    assert status == ""
    assert dirty is True
