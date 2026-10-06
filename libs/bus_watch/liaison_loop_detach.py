"""Detach helpers for attended liaison --loop (a:38446)."""

from __future__ import annotations

import os
import time
from pathlib import Path


def clear_loop_pid_files(
    *,
    pid_env: str = "LIAISON_LOOP_PID_FILE",
    meta_env: str = "LIAISON_LOOP_META_FILE",
) -> None:
    """Drop pid/meta so re-arm cannot SIGTERM a reused PID (a:38446 B3)."""
    for key in (pid_env, meta_env):
        raw = os.environ.get(key) or ""
        if not raw:
            continue
        try:
            Path(raw).unlink(missing_ok=True)
        except OSError:
            pass


def monitor_heartbeat_stale(path: str, *, poll_s: int) -> bool:
    """True when the attended monitor stopped touching ``path`` (a:38446 B1)."""
    if not path:
        return False
    try:
        age = time.time() - Path(path).stat().st_mtime
    except OSError:
        return True
    return age > max(float(poll_s) * 2.0, 90.0)
