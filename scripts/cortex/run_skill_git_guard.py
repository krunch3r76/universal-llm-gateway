#!/usr/bin/env python3
"""CLI entry for skill git guard (pre-commit / CI)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPTS_CORTEX = Path(__file__).resolve().parent
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from _skill_git_guard import run_skill_git_guard  # noqa: E402


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        repo_root = Path(out)
    except subprocess.CalledProcessError:
        pass
    return run_skill_git_guard(repo_root)


if __name__ == "__main__":
    sys.exit(main())
