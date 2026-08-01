"""Fail-loud python resolution for git pre-commit hooks."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_RESOLVER = _REPO / "scripts" / "hooks" / "resolve_hook_python.sh"


def _run_resolver(*, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    bash = os.environ.get("BASH", "/bin/bash")
    return subprocess.run(
        [bash, str(_RESOLVER)],
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.offline
def test_missing_interpreter_fails_loud() -> None:
    """AC2: absent python must exit 1 with FATAL, never 0."""
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env["PATH"] = "/nonexistent"
    env["HOME"] = "/tmp/nonexistent-hook-home-6627"
    result = _run_resolver(env=env)
    assert result.returncode == 1
    assert "FATAL: pre-commit hook: no executable python3" in result.stderr


@pytest.mark.offline
def test_path_python_wins_over_missing_home_venv() -> None:
    """Dispatch seats: PATH operator venv resolves when HOME venv is absent."""
    real_python = subprocess.run(
        ["bash", "-lc", "command -v python3"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert real_python

    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env["PATH"] = f"{Path(real_python).parent}:/usr/bin:/bin"
    env["HOME"] = "/tmp/nonexistent-hook-home-6627"
    result = _run_resolver(env=env)
    assert result.returncode == 0
    assert result.stdout.strip() == real_python
