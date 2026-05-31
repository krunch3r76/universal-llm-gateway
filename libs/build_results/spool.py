"""Spool location, result_ref block, spool writer, and mtime prune.

BUILD_RESULTS_DIR defaults under the workspaces sandbox root so the spool is
reachable as fs(sandbox="workspaces", path="ulg-build-results/{id}/...") from
every seat (decision #1). Env overrides allow container/test relocation.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from build_results.signals import compute_signals

logger = get_logger(__name__)

# Workspaces sandbox root == /mnt/torus/projects; the spool is a non-repo sibling
# of ulg-grok-worktrees so it pollutes no git tree and needs no new mount.
WORKSPACES_RELATIVE_ROOT = "ulg-build-results"
BUILD_RESULTS_DIR: Path = Path(
    os.getenv("BUILD_RESULTS_DIR", "/mnt/torus/projects/ulg-build-results")
).expanduser()

RETENTION_SECONDS: int = int(
    os.getenv("BUILD_RESULTS_RETENTION_SECONDS", str(7 * 24 * 60 * 60))
)


def result_dir(dispatch_id: str) -> Path:
    """Absolute spool directory for a dispatch (not created here)."""
    return BUILD_RESULTS_DIR / dispatch_id


def result_ref(dispatch_id: str) -> dict[str, Any]:
    """Build the seat-agnostic pointer block embedded in build envelopes.

    Paths are RELATIVE to the workspaces sandbox root so any seat reads them via
    fs(sandbox="workspaces", op="read", path=...). No absolute host paths leak.
    """
    rel = f"{WORKSPACES_RELATIVE_ROOT}/{dispatch_id}"
    return {
        "dispatch_id": dispatch_id,
        "sandbox": "workspaces",
        "result_dir": f"{rel}/",
        "signals_path": f"{rel}/signals.json",
        "envelope_path": f"{rel}/envelope.json",
        "sidecar_path": f"{rel}/sidecar.ndjson",
    }


def write_spool(
    dispatch_id: str,
    envelope: dict[str, Any],
    *,
    sidecar_src: str | Path | None = None,
) -> dict[str, Any]:
    """Write signals.json + envelope.json (+ copied sidecar) for a terminal dispatch.

    Returns the computed signals dict (so the caller can reuse it without a
    re-read). Best-effort: a spool-write failure is logged and swallowed — the
    canonical sidecar + envelope remain the durable source of truth, so a spool
    miss must never fail the dispatch.
    """
    signals = compute_signals(envelope)
    try:
        d = result_dir(dispatch_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "signals.json").write_text(
            json.dumps(signals, indent=2, default=str), encoding="utf-8"
        )
        (d / "envelope.json").write_text(
            json.dumps(envelope, indent=2, default=str), encoding="utf-8"
        )
        if sidecar_src is not None:
            src = Path(sidecar_src)
            if src.exists():
                shutil.copy2(src, d / "sidecar.ndjson")
    except OSError as exc:
        logger.warning("build_results spool write failed for %s: %s", dispatch_id, exc)
    return signals


def prune_spool(retention_seconds: int = RETENTION_SECONDS) -> int:
    """Remove spool dirs whose mtime is older than the retention window.

    Called on worker startup (decision #2). Returns the number of dirs removed.
    Never raises — prune failures must not block boot.
    """
    if retention_seconds <= 0 or not BUILD_RESULTS_DIR.exists():
        return 0
    now = time.time()
    removed = 0
    for child in BUILD_RESULTS_DIR.iterdir():
        if not child.is_dir():
            continue
        try:
            age = now - child.stat().st_mtime
        except OSError:
            continue
        if age > retention_seconds:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed
