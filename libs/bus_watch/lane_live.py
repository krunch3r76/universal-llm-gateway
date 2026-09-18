"""OLN live tri-state — process-start probe, never inferred from landed SHA alone."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Protocol

import psutil

_REPO = Path(__file__).resolve().parents[2]
_TICK = "liaison-tick.py"


class LiveInspect(Protocol):
    def ticker_start_unix(self, root: str) -> float | None: ...
    def commit_unix(self, sha: str) -> float | None: ...
    def sha_on_head(self, sha: str) -> bool | None: ...


def ticker_start_unix(root: str) -> float | None:
    """Create-time of ``liaison-tick.py --root <root> --spawn-on-wake``, if any."""
    want = str(root or "").strip()
    if not want:
        return None
    for proc in psutil.process_iter(["pid", "cmdline", "create_time"]):
        argv = proc.info.get("cmdline") or []
        if _TICK not in " ".join(argv) or "--spawn-on-wake" not in argv:
            continue
        if _root_of(argv) != want:
            continue
        started = proc.info.get("create_time")
        if started is None:
            return None
        return float(started)
    return None


def _root_of(argv: list[str]) -> str | None:
    for i, tok in enumerate(argv):
        if tok == "--root" and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith("--root="):
            return tok.split("=", 1)[1]
    return None


def commit_unix(sha: str) -> float | None:
    ref = str(sha or "").strip()
    if not ref:
        return None
    try:
        out = subprocess.run(  # noqa: S603 — fixed argv, our repo
            ["git", "-C", str(_REPO), "log", "-1", "--format=%ct", ref],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if out.returncode != 0:
        return None
    text = out.stdout.strip()
    if not text.isdigit():
        return None
    return float(text)


def sha_on_head(sha: str) -> bool | None:
    ref = str(sha or "").strip()
    if not ref:
        return None
    try:
        out = subprocess.run(  # noqa: S603 — fixed argv, our repo
            ["git", "-C", str(_REPO), "merge-base", "--is-ancestor", ref, "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if out.returncode == 0:
        return True
    if out.returncode == 1:
        return False
    return None


def probe_live(
    lane_id: Any,
    landed_sha: str,
    *,
    parent_root: str | None = None,
    inspect: LiveInspect | None = None,
) -> str:
    """Return ``unprobed`` / ``live`` / ``stale``. SHA alone is never ``live``."""
    _ = lane_id
    if not str(landed_sha or "").strip():
        return "unprobed"
    root = str(parent_root or "").strip()
    if not root:
        return "unprobed"
    start_fn = inspect.ticker_start_unix if inspect else ticker_start_unix
    commit_fn = inspect.commit_unix if inspect else commit_unix
    ancestor_fn = inspect.sha_on_head if inspect else sha_on_head
    started = start_fn(root)
    committed = commit_fn(landed_sha)
    on_head = ancestor_fn(landed_sha)
    if started is None or committed is None or on_head is None:
        return "unprobed"
    if not on_head:
        return "stale"
    if started > committed:
        return "live"
    return "stale"
