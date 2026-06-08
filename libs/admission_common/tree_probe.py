"""Shared working-tree probe for admission surfaces."""

from __future__ import annotations

import subprocess

from universal_logging import get_logger

logger = get_logger(__name__)


def probe_working_tree(cwd: str) -> tuple[str, bool]:
    """Return (porcelain status, dirty). Fail-safe: dirty=True on probe errors."""
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("git status probe failed for cwd=%s: %s", cwd, exc)
        return "", True
    status = proc.stdout
    return status, bool(status.strip())
