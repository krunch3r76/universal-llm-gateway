"""Checkout porcelain disclosure for host-process /health — not live-SHA proof.

``code_version`` is a Git ancestry label. A dirty tree with ``HEAD`` equal is
a legal pair; this module names that dirt so consumers cannot treat equal as
proof-of-live. Callers: ``cdp_ask`` ``HealthResponse``. Does not speak
running-bytes identity or ``live_sha_claim``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Literal

from universal_workspace import get_workspace_root

TreeState = Literal["dirty", "clean", "unknown"]


def resolve_tree_state(root: Path | None = None) -> TreeState:
    """Return dirty|clean|unknown from ``git status --porcelain``.

    Non-empty porcelain (including untracked) is dirty. Unreadable git is
    unknown — not clean. Evidence is porcelain only.
    """
    try:
        workspace = root if root is not None else get_workspace_root()
    except RuntimeError:
        return "unknown"
    try:
        proc = subprocess.run(
            ["git", "-C", str(workspace), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return "dirty" if proc.stdout.strip() else "clean"
