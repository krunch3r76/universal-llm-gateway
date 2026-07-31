"""Tests for check-team-dispatch-surface-drift.py."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKER = REPO_ROOT / "scripts/check-team-dispatch-surface-drift.py"
CANONICAL = REPO_ROOT / "config/mcp/canonical.yaml"


def _run(*extra: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(CHECKER), *extra]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_current_tree_passes() -> None:
    result = _run()
    assert result.returncode == 0, result.stderr or result.stdout


def test_seeded_drift_fails(tmp_path: Path) -> None:
    copy = tmp_path / "canonical.yaml"
    shutil.copy(CANONICAL, copy)
    text = copy.read_text(encoding="utf-8")
    copy.write_text(
        text.replace("      server_tools: {type: boolean}\n", "", 1),
        encoding="utf-8",
    )
    result = _run("--canonical-yaml", str(copy))
    assert result.returncode == 1
    assert "server_tools" in result.stderr
    assert "team_dispatch_generate" in result.stderr
