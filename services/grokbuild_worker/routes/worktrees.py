"""Worktree and git-op routes for grokbuild-worker.

Handles six endpoints:
  POST   /worktrees                      → worktree_create_op
  GET    /worktrees                      → worktree_list_op
  DELETE /worktrees/{name}               → worktree_remove_op
  POST   /worktrees/{name}/push          → push_op
  POST   /worktrees/{name}/pull-requests → pr_create_op
  POST   /snapshots                      → snapshot_op

``{name}`` is the short worktree name; the handler derives the cwd from
``WORKTREE_ROOT/<name>``. All responses surface the raw lib envelope so
callers get the canonical shape without re-wrapping.
"""

from __future__ import annotations

import os
import time
from typing import Any

import grokbuild.worktree as _wt
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from grokbuild import (
    pr_create_op,
    push_op,
    snapshot_op,
    worktree_create_op,
    worktree_list_op,
    worktree_remove_op,
)

from services.grokbuild_worker.error_map import raise_if_error
from services.grokbuild_worker.events import (
    GrokbuildPRCreated,
    GrokbuildPushCompleted,
    GrokbuildSnapshotCreated,
    GrokbuildSnapshotMainReset,
    GrokbuildWorktreeCreated,
    GrokbuildWorktreeListed,
    GrokbuildWorktreeRemoved,
    envelope_outcome,
    publish_nowait,
)
from services.grokbuild_worker.models.sync import (
    PRCreateRequest,
    PushRequest,
    SnapshotRequest,
    WorktreeCreateRequest,
)

router = APIRouter(prefix="/api/v1/grokbuild", tags=["grokbuild-worktrees"])


def _worktree_cwd(name: str) -> str:
    """Resolve absolute worktree path from short name."""
    return os.path.join(_wt.WORKTREE_ROOT, name)


def _meta_int(envelope: dict[str, Any], key: str) -> int:
    """Read an integer metadata field; tolerate missing/None/garbage."""
    value = envelope.get("metadata", {}).get(key)
    if isinstance(value, int):
        return value
    return 0


@router.post("/worktrees")
async def create_worktree(req: WorktreeCreateRequest) -> JSONResponse:
    """Create a git worktree under WORKTREE_ROOT/<name>."""
    t0 = time.monotonic()
    envelope = await worktree_create_op(
        name=req.name,
        branch=req.branch,
        source_repo=req.source_repo,
        create_branch=req.create_branch,
        start_point=req.start_point,
    )
    raise_if_error(envelope)
    duration_s = time.monotonic() - t0
    publish_nowait(
        GrokbuildWorktreeCreated(
            name=req.name,
            branch=req.branch,
            duration_s=duration_s,
            outcome=envelope_outcome(envelope),
        )
    )
    return JSONResponse(content=envelope)


@router.post("/snapshots")
async def create_snapshot(req: SnapshotRequest) -> JSONResponse:
    """Snapshot the main-tree diff into an arc/<slug> worktree."""
    t0 = time.monotonic()
    envelope = await snapshot_op(
        source_repo=req.source_repo,
        slug=req.slug,
        name=req.name,
        reset_main=req.reset_main,
    )
    raise_if_error(envelope)
    duration_s = time.monotonic() - t0
    meta = envelope.get("metadata", {})
    publish_nowait(
        GrokbuildSnapshotCreated(
            slug=req.slug,
            branch=meta.get("branch", ""),
            worktree_path=meta.get("worktree_path", ""),
            snapshot_sha=meta.get("snapshot_sha", ""),
            duration_s=duration_s,
            outcome=envelope_outcome(envelope),
        )
    )
    if meta.get("main_reset") == "ok":
        publish_nowait(
            GrokbuildSnapshotMainReset(
                slug=req.slug, source_repo=meta.get("source_repo", "")
            )
        )
    return JSONResponse(content=envelope)


@router.get("/worktrees")
async def list_worktrees() -> JSONResponse:
    """Enumerate all grokbuild-managed worktrees."""
    t0 = time.monotonic()
    envelope = await worktree_list_op()
    raise_if_error(envelope)
    duration_s = time.monotonic() - t0
    count: int = envelope.get("metadata", {}).get("count", 0)
    publish_nowait(GrokbuildWorktreeListed(count=count, duration_s=duration_s))
    return JSONResponse(content=envelope)


@router.delete("/worktrees/{name}")
async def remove_worktree(name: str) -> JSONResponse:
    """Remove a worktree by name (must be clean and not in-flight)."""
    t0 = time.monotonic()
    envelope = await worktree_remove_op(name=name)
    raise_if_error(envelope)
    duration_s = time.monotonic() - t0
    publish_nowait(
        GrokbuildWorktreeRemoved(
            name=name,
            duration_s=duration_s,
            outcome=envelope_outcome(envelope),
        )
    )
    return JSONResponse(content=envelope)


@router.post("/worktrees/{name}/push")
async def push_worktree(name: str, req: PushRequest) -> JSONResponse:
    """Push the current branch of a worktree to a remote."""
    t0 = time.monotonic()
    cwd = _worktree_cwd(name)
    envelope = await push_op(
        cwd=cwd,
        remote=req.remote,
        branch=req.branch,
        set_upstream=req.set_upstream,
    )
    raise_if_error(envelope)
    duration_s = time.monotonic() - t0
    meta = envelope.get("metadata", {})
    branch: str = meta.get("branch", "")
    publish_nowait(
        GrokbuildPushCompleted(
            name=name,
            branch=branch,
            duration_s=duration_s,
            outcome=envelope_outcome(envelope),
            commits_pushed=_meta_int(envelope, "commits_pushed"),
        )
    )
    return JSONResponse(content=envelope)


@router.post("/worktrees/{name}/pull-requests")
async def create_pull_request(name: str, req: PRCreateRequest) -> JSONResponse:
    """Open a GitHub PR from a worktree branch via gh."""
    t0 = time.monotonic()
    cwd = _worktree_cwd(name)
    envelope = await pr_create_op(
        cwd=cwd,
        pr_title=req.pr_title,
        pr_body=req.pr_body,
        pr_base=req.pr_base,
        pr_head=req.pr_head,
        draft=req.draft,
    )
    raise_if_error(envelope)
    duration_s = time.monotonic() - t0
    pr_number = envelope.get("metadata", {}).get("pr_number")
    publish_nowait(
        GrokbuildPRCreated(
            name=name,
            pr_number=pr_number if isinstance(pr_number, int) else None,
            duration_s=duration_s,
            outcome=envelope_outcome(envelope),
        )
    )
    return JSONResponse(content=envelope)
