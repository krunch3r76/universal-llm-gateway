"""``worktree_list_op`` handler — enumerate grokbuild-managed worktrees.

Scoped to ``WORKTREE_ROOT`` (the Phase 2 hardcoded root) — does not list
worktrees registered elsewhere on the host. Enumeration walks the filesystem
directly rather than invoking ``git worktree list``: WORKTREE_ROOT is the
single source of truth for ``op="worktree_create"`` outputs, so reading it
directly gives the authoritative set and avoids parsing git's text format.

For each entry we still issue a minimal per-worktree git call
(``status --porcelain=v2 --branch``) to pick up branch, HEAD SHA, and dirty
status in one round-trip. The in-flight flag comes from the persistent
registry (``cwds_under``) so an active dispatch is visible to the operator
before they attempt a ``worktree_remove``.

No admission gate — the only error path is filesystem failure on
``WORKTREE_ROOT`` itself, which maps to ``reason_code="worktree_root_unreachable"``.
A missing root directory is NOT an error: it just yields an empty list (the
root is created lazily by ``worktree_create``).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
import uuid
from typing import Any

import grokbuild.worktree as _wt
from grokbuild.envelope import _metadata_base
from grokbuild.events import (
    emit_grok_build_list_called,
    emit_grok_build_list_completed,
    emit_grok_build_list_failed,
)
from grokbuild.registry import cwds_under


def _read_worktree_state(path: str) -> dict[str, Any]:
    """Best-effort git probe for one worktree directory.

    Returns ``{branch, head_sha, dirty, valid}``. ``valid=False`` means the
    path exists under WORKTREE_ROOT but isn't a usable git worktree (e.g. a
    half-removed directory or a stray folder a human left behind). We still
    surface it so the operator sees it.
    """
    branch = ""
    head_sha = ""
    dirty = False
    valid = False
    try:
        proc = subprocess.run(
            ["git", "-C", path, "status", "--porcelain=v2", "--branch"],
            check=True,
            capture_output=True,
            text=True,
            timeout=_wt._GIT_TIMEOUT,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return {"branch": branch, "head_sha": head_sha, "dirty": dirty, "valid": valid}

    valid = True
    for line in proc.stdout.splitlines():
        if line.startswith("# branch.head "):
            branch = line[len("# branch.head ") :].strip()
        elif line.startswith("# branch.oid "):
            head_sha = line[len("# branch.oid ") :].strip()
            if head_sha == "(initial)":
                head_sha = ""
        elif line and not line.startswith("#"):
            dirty = True
    return {"branch": branch, "head_sha": head_sha, "dirty": dirty, "valid": valid}


def _enumerate(root: str) -> list[dict[str, Any]]:
    """Return one entry per immediate child directory of ``root``.

    Synchronous fallback retained for tests and rare contexts without a
    running event loop. The async path (:func:`_enumerate_async`) is
    preferred — it fans out the per-worktree subprocess probes concurrently
    via the default thread-pool executor, amortizing ~50ms × N down to ~50ms
    when there are several worktrees.
    """
    entries: list[dict[str, Any]] = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isdir(path) or os.path.islink(path):
            continue
        state = _read_worktree_state(path)
        entries.append(
            {
                "name": name,
                "path": path,
                "branch": state["branch"],
                "head_sha": state["head_sha"],
                "dirty": state["dirty"],
                "valid": state["valid"],
                "in_flight": False,
                "dispatch_id": None,
            }
        )
    return entries


async def _enumerate_async(root: str) -> list[dict[str, Any]]:
    """Async, concurrent variant of :func:`_enumerate` (review S5).

    The per-worktree ``_read_worktree_state`` probes each spawn ``git
    status --porcelain=v2 --branch`` — for N worktrees the sequential
    path is N × ~50ms. Fanning out via ``asyncio.gather`` over the
    thread-pool executor brings the total to ~max(per-probe time).
    """
    loop = asyncio.get_running_loop()
    names = sorted(await loop.run_in_executor(None, os.listdir, root))

    # Cheap sync filter for non-dir / symlink entries — runs in the calling
    # task so the fan-out only covers real subprocess work.
    paths: list[tuple[str, str]] = []
    for name in names:
        path = os.path.join(root, name)
        if not os.path.isdir(path) or os.path.islink(path):
            continue
        paths.append((name, path))

    if not paths:
        return []

    states = await asyncio.gather(
        *(loop.run_in_executor(None, _read_worktree_state, p) for _, p in paths)
    )
    return [
        {
            "name": name,
            "path": path,
            "branch": state["branch"],
            "head_sha": state["head_sha"],
            "dirty": state["dirty"],
            "valid": state["valid"],
            "in_flight": False,
            "dispatch_id": None,
        }
        for (name, path), state in zip(paths, states, strict=True)
    ]


def _list_metadata(
    *, worktree_root: str, worktrees: list[dict[str, Any]]
) -> dict[str, Any]:
    meta = _metadata_base(mode="", cwd="", session_id=None, model=None)
    meta.update(
        worktree_root=worktree_root,
        worktrees=worktrees,
        count=len(worktrees),
    )
    return meta


def _envelope(
    *,
    dispatch_id: str,
    status: str,
    duration_s: float,
    meta: dict[str, Any],
    reason_code: str = "",
    reason: str = "",
    stderr: str = "",
) -> dict[str, Any]:
    meta.update(reason_code=reason_code, reason=reason)
    return {
        "dispatch_id": dispatch_id,
        "status": status,
        "stdout": "",
        "stderr": stderr,
        "exit_code": 0 if status == "completed" else None,
        "duration_s": duration_s,
        "sidecar_path": None,
        "metadata": meta,
    }


async def worktree_list_op() -> dict[str, Any]:
    """Enumerate worktrees under ``WORKTREE_ROOT``.

    Empty root → ``status="completed"`` with ``count=0``. Filesystem failure
    on the root → ``status="failed"`` with ``reason_code="worktree_root_unreachable"``.

    Event-emit ordering matches ``grokbuild.dispatch``: ``.called`` fires
    AFTER admission (the root-existence check). Aligned across worktree
    ops in V2 close-out (review W9).
    """
    dispatch_id = str(uuid.uuid4())
    t0 = time.monotonic()
    root = _wt.WORKTREE_ROOT

    loop = asyncio.get_running_loop()
    try:
        root_exists = await loop.run_in_executor(None, os.path.isdir, root)
        if not root_exists:
            emit_grok_build_list_called(dispatch_id=dispatch_id, worktree_root=root)
            duration_s = time.monotonic() - t0
            meta = _list_metadata(worktree_root=root, worktrees=[])
            emit_grok_build_list_completed(
                dispatch_id=dispatch_id,
                duration_s=duration_s,
                worktree_root=root,
                count=0,
            )
            return _envelope(
                dispatch_id=dispatch_id,
                status="completed",
                duration_s=duration_s,
                meta=meta,
            )
        emit_grok_build_list_called(dispatch_id=dispatch_id, worktree_root=root)
        entries = await _enumerate_async(root)
    except OSError as exc:
        duration_s = time.monotonic() - t0
        meta = _list_metadata(worktree_root=root, worktrees=[])
        emit_grok_build_list_failed(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            error=str(exc)[:200],
            worktree_root=root,
        )
        return _envelope(
            dispatch_id=dispatch_id,
            status="failed",
            duration_s=duration_s,
            meta=meta,
            reason_code="worktree_root_unreachable",
            reason=f"failed to enumerate {root}: {exc}",
            stderr=str(exc),
        )

    in_flight = await cwds_under(root)
    for entry in entries:
        canonical = os.path.realpath(entry["path"])
        matched_dispatch: str | None = None
        if canonical in in_flight:
            matched_dispatch = in_flight[canonical] or None
        else:
            prefix = canonical + os.sep
            for cwd, did in in_flight.items():
                if cwd.startswith(prefix):
                    matched_dispatch = did or None
                    break
        # Membership in `in_flight` is the authoritative signal; dispatch_id
        # may still be None when the registry record carries no uuid (legacy
        # / test fixture pre-population).
        in_flight_hit = canonical in in_flight or any(
            cwd.startswith(canonical + os.sep) for cwd in in_flight
        )
        entry["in_flight"] = in_flight_hit
        entry["dispatch_id"] = matched_dispatch if in_flight_hit else None

    duration_s = time.monotonic() - t0
    meta = _list_metadata(worktree_root=root, worktrees=entries)
    emit_grok_build_list_completed(
        dispatch_id=dispatch_id,
        duration_s=duration_s,
        worktree_root=root,
        count=len(entries),
    )
    return _envelope(
        dispatch_id=dispatch_id,
        status="completed",
        duration_s=duration_s,
        meta=meta,
    )
