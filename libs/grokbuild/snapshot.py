"""``snapshot_op`` — lift the uncommitted main-tree diff into an arc worktree.

Mechanism (design Q1): capture the full combined working state (HEAD + staged +
unstaged + untracked-non-ignored) as a single tree using an isolated scratch
index, commit it off HEAD, then provision an ``arc/<slug>`` worktree at that
commit via ``worktree_create_op`` (design Q3). Optionally reset the main tree
clean (design Q2, opt-in ``reset_main``). The snapshot commit is the durable
capture and always exists before the main tree is touched.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
import time
import uuid
from typing import Any

from grokbuild.events_snapshot import (
    emit_grok_build_snapshot_called,
    emit_grok_build_snapshot_completed,
    emit_grok_build_snapshot_failed,
    emit_grok_build_snapshot_rejected,
)
from grokbuild.worktree import _validate_source_repo, worktree_create_op
from grokbuild.worktree_schema import _GIT_TIMEOUT, _envelope, _validate_name

_SNAPSHOT_MSG = "snapshot: {slug}"


async def _run_git(
    args: list[str], cwd: str, env: dict[str, str]
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        cwd,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=_GIT_TIMEOUT)
    return (
        proc.returncode or 0,
        out_b.decode(errors="replace"),
        err_b.decode(errors="replace"),
    )


async def _capture_snapshot_commit(source_repo: str, slug: str) -> tuple[str, str]:
    """Return (snapshot_sha, reason). reason empty on success.

    Uses a scratch GIT_INDEX_FILE so the real index + working tree are never
    mutated. ``add -A`` honors .gitignore. Empty diff → reason='clean_tree'.
    """
    env = dict(os.environ)
    fd, index_path = tempfile.mkstemp(prefix="grokbuild-snap-index-")
    os.close(fd)
    env["GIT_INDEX_FILE"] = index_path
    try:
        rc, _, err = await _run_git(["read-tree", "HEAD"], source_repo, env)
        if rc != 0:
            return "", f"read-tree failed: {err.strip()}"
        rc, _, err = await _run_git(["add", "-A"], source_repo, env)
        if rc != 0:
            return "", f"add -A failed: {err.strip()}"
        rc, tree_out, err = await _run_git(["write-tree"], source_repo, env)
        if rc != 0:
            return "", f"write-tree failed: {err.strip()}"
        snap_tree = tree_out.strip()
        rc, head_tree_out, _ = await _run_git(
            ["rev-parse", "HEAD^{tree}"], source_repo, env
        )
        if rc == 0 and head_tree_out.strip() == snap_tree:
            return "", "clean_tree"
        rc, commit_out, err = await _run_git(
            [
                "commit-tree",
                snap_tree,
                "-p",
                "HEAD",
                "-m",
                _SNAPSHOT_MSG.format(slug=slug),
            ],
            source_repo,
            env,
        )
        if rc != 0:
            return "", f"commit-tree failed: {err.strip()}"
        return commit_out.strip(), ""
    finally:
        with contextlib.suppress(OSError):
            os.unlink(index_path)


async def _reset_main(source_repo: str) -> tuple[bool, str]:
    """git reset --hard HEAD && git clean -fd. Returns (ok, reason)."""
    env = dict(os.environ)
    rc, _, err = await _run_git(["reset", "--hard", "HEAD"], source_repo, env)
    if rc != 0:
        return False, f"reset failed: {err.strip()}"
    rc, _, err = await _run_git(["clean", "-fd"], source_repo, env)
    if rc != 0:
        return False, f"clean failed: {err.strip()}"
    return True, ""


async def snapshot_op(
    *,
    source_repo: str,
    slug: str,
    name: str = "",
    reset_main: bool = False,
) -> dict[str, Any]:
    """Snapshot the main-tree diff into ``arc/<slug>`` worktree.

    ``name`` defaults to ``slug`` (worktree short name). ``reset_main=True``
    (opt-in, destructive) resets the main tree clean AFTER the worktree commit
    is durable; a reset failure never destroys the snapshot.
    """
    dispatch_id = str(uuid.uuid4())
    t0 = time.monotonic()
    wt_name = name or slug
    branch = f"arc/{slug}"

    slug_err = _validate_name(slug)
    if slug_err:
        return _reject(dispatch_id, "slug_invalid", slug_err, source_repo, slug, branch)
    canonical, src_err = _validate_source_repo(source_repo)
    if src_err:
        return _reject(
            dispatch_id, "source_repo_invalid", src_err, source_repo, slug, branch
        )

    emit_grok_build_snapshot_called(
        dispatch_id=dispatch_id,
        source_repo=canonical,
        slug=slug,
        branch=branch,
        reset_main=reset_main,
    )

    snap_sha, reason = await _capture_snapshot_commit(canonical, slug)
    if reason == "clean_tree":
        return _reject(
            dispatch_id,
            "clean_tree",
            "main tree has no uncommitted changes",
            canonical,
            slug,
            branch,
        )
    if reason:
        return _fail(dispatch_id, t0, reason, canonical, slug, branch)

    wt_env = await worktree_create_op(
        name=wt_name,
        branch=branch,
        source_repo=canonical,
        create_branch=True,
        start_point=snap_sha,
    )
    if wt_env.get("status") != "completed":
        meta = wt_env.get("metadata", {})
        return _fail(
            dispatch_id,
            t0,
            f"worktree_create {meta.get('reason_code', '')}: {meta.get('reason', '')}",
            canonical,
            slug,
            branch,
        )
    worktree_path = wt_env["metadata"]["worktree_path"]

    main_reset = "skipped"
    main_reset_reason = ""
    if reset_main:
        ok, reset_reason = await _reset_main(canonical)
        main_reset = "ok" if ok else "failed"
        main_reset_reason = reset_reason

    duration_s = time.monotonic() - t0
    emit_grok_build_snapshot_completed(
        dispatch_id=dispatch_id,
        duration_s=duration_s,
        source_repo=canonical,
        slug=slug,
        branch=branch,
        worktree_path=worktree_path,
        snapshot_sha=snap_sha,
        main_reset=main_reset,
    )
    meta = {
        "slug": slug,
        "branch": branch,
        "worktree_path": worktree_path,
        "snapshot_sha": snap_sha,
        "source_repo": canonical,
        "main_reset": main_reset,
        "main_reset_reason": main_reset_reason,
    }
    return _envelope(
        dispatch_id=dispatch_id,
        status="completed",
        stdout=snap_sha,
        stderr="",
        exit_code=0,
        duration_s=duration_s,
        meta=meta,
    )


def _reject(
    dispatch_id: str,
    code: str,
    reason: str,
    source_repo: str,
    slug: str,
    branch: str,
) -> dict[str, Any]:
    emit_grok_build_snapshot_rejected(
        dispatch_id=dispatch_id,
        reason_code=code,
        reason=reason,
        source_repo=source_repo,
        slug=slug,
        branch=branch,
    )
    return _envelope(
        dispatch_id=dispatch_id,
        status="rejected",
        stdout="",
        stderr="",
        exit_code=None,
        duration_s=0.0,
        meta={"slug": slug, "branch": branch, "source_repo": source_repo},
        reason_code=code,
        reason=reason,
    )


def _fail(
    dispatch_id: str,
    t0: float,
    reason: str,
    source_repo: str,
    slug: str,
    branch: str,
) -> dict[str, Any]:
    duration_s = time.monotonic() - t0
    emit_grok_build_snapshot_failed(
        dispatch_id=dispatch_id,
        duration_s=duration_s,
        error=reason[:200],
        source_repo=source_repo,
        slug=slug,
        branch=branch,
    )
    return _envelope(
        dispatch_id=dispatch_id,
        status="failed",
        stdout="",
        stderr=reason,
        exit_code=None,
        duration_s=duration_s,
        meta={"slug": slug, "branch": branch, "source_repo": source_repo},
        reason_code="snapshot_failed",
        reason=reason,
    )
