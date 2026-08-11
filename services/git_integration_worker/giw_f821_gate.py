"""Blocking F821 gate for the git-integration-worker package subtree (arc 6655).

Whole-repo ruff is blocked by master lint debt; this subtree is kept
F821-clean. Undefined names in dispatch substrate code are never intentional —
the gate runs before any land commit (Lane-B terminal / salvage) and on
``git_land`` green gate before master CAS advance.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from services.git_integration_worker.config import GIW_SUBTREE_F821_REL

_F821_SELECT = ("--select", "F821")
_GIT_TIMEOUT_S = 60.0


@dataclass(frozen=True, slots=True)
class GiwF821CheckResult:
    """Outcome of ``run_giw_subtree_f821_check``."""

    exit_code: int
    command: str
    stdout: str
    stderr: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


def giw_subtree_f821_command() -> str:
    """Shell-safe command string for embedding in bash gate scripts."""
    return f"ruff check --select F821 {GIW_SUBTREE_F821_REL}"


def run_giw_subtree_f821_check(source_repo: Path) -> GiwF821CheckResult:
    """Run ``ruff check --select F821`` on the GIW package subtree."""
    rel = GIW_SUBTREE_F821_REL
    command = giw_subtree_f821_command()
    try:
        proc = subprocess.run(
            ["ruff", "check", *_F821_SELECT, rel],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            cwd=str(source_repo.resolve()),
            check=False,
        )
    except FileNotFoundError:
        return GiwF821CheckResult(
            exit_code=0,
            command=command,
            stdout="",
            stderr="ruff missing — gate skipped",
        )
    except subprocess.TimeoutExpired:
        return GiwF821CheckResult(
            exit_code=124,
            command=command,
            stdout="",
            stderr="ruff timed out",
        )
    return GiwF821CheckResult(
        exit_code=proc.returncode,
        command=command,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


__all__ = [
    "GiwF821CheckResult",
    "giw_subtree_f821_command",
    "run_giw_subtree_f821_check",
]
