"""Git HEAD resolution and admit→closeout diff helpers (6341 L2)."""

from __future__ import annotations

import subprocess
from pathlib import Path


def resolve_git_head(source_repo: Path) -> str | None:
    """Resolved ``git rev-parse HEAD`` for *source_repo*, or ``None`` when absent."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    sha = proc.stdout.decode("utf-8", errors="replace").strip()
    return sha or None


def git_diff_paths_between(
    source_repo: Path,
    *,
    admit_head: str | None,
    closeout_head: str | None,
) -> frozenset[str]:
    """Paths in ``git diff --name-only admit_head..closeout_head`` (once per closeout)."""
    if not admit_head or not closeout_head:
        return frozenset()
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(source_repo),
                "diff",
                "--name-only",
                f"{admit_head}..{closeout_head}",
            ],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return frozenset()
    return frozenset(
        chunk.decode("utf-8", errors="replace")
        for chunk in proc.stdout.splitlines()
        if chunk
    )
