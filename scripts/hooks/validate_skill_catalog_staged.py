#!/usr/bin/env python3
"""Run skill-catalog parity check only when staged paths touch census/catalog/SOT."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_STAGED_PREFIXES = (
    "cursor-plugins/ulg-ecosystem/SKILLS_CENSUS.txt",
    "config/skills.yaml",
    ".cursor/skills/",
)


def _staged_paths() -> list[str]:
    proc = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=_REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _touches_skill_catalog_paths(staged: list[str]) -> bool:
    for path in staged:
        if path == _STAGED_PREFIXES[0] or path == _STAGED_PREFIXES[1]:
            return True
        if path.startswith(_STAGED_PREFIXES[2]) and path.endswith("SKILL.md"):
            return True
    return False


def main() -> int:
    staged = _staged_paths()
    if not staged or not _touches_skill_catalog_paths(staged):
        return 0
    validator = _REPO / "scripts" / "cortex" / "validate_skill_catalog.py"
    proc = subprocess.run(
        [sys.executable, str(validator), "--root", str(_REPO)],
        cwd=_REPO,
        check=False,
    )
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
