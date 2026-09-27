"""Checkout porcelain disclosure for host-process /health — not live-SHA proof.

``code_version`` is a Git ancestry label. A dirty tree with ``HEAD`` equal is
a legal pair; this module names that dirt so consumers cannot treat equal as
proof-of-live. Callers: ``cdp_ask`` ``HealthResponse``. Does not speak
running-bytes identity or ``live_sha_claim``.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import Literal

from universal_workspace import get_workspace_root

TreeState = Literal["dirty", "clean", "unknown"]

_TREE_STATE_CACHE_TTL_S = 30.0

_cache_lock = threading.Lock()
_cached_state: TreeState | None = None
_cached_at: float | None = None


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


def peek_tree_state() -> TreeState:
    """Return cached tree state for request paths; miss or TTL → unknown."""
    with _cache_lock:
        if _cached_at is None or _cached_state is None:
            return "unknown"
        if time.monotonic() - _cached_at > _TREE_STATE_CACHE_TTL_S:
            return "unknown"
        return _cached_state


def note_tree_state(state: TreeState) -> None:
    """Record a tree-state sample for ``peek_tree_state``."""
    global _cached_state, _cached_at
    with _cache_lock:
        _cached_state = state
        _cached_at = time.monotonic()


def refresh_tree_state(root: Path | None = None) -> TreeState:
    """Run ``resolve_tree_state`` off the request path and refresh the cache."""
    state = resolve_tree_state(root)
    note_tree_state(state)
    return state


def _reset_tree_state_cache_for_tests() -> None:
    """Clear the in-process cache (tests only)."""
    global _cached_state, _cached_at
    with _cache_lock:
        _cached_state = None
        _cached_at = None
