"""Integrate, status, and diff routes for git-integration-worker."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from git_integrate.commit import commit_op
from git_integrate.events import emit_git_status_read
from git_integrate.git_cas import (
    commit_exists,
    is_dirty,
    is_reachable_from_master,
    land_diff_numstat,
    land_diff_text,
    land_fingerprint,
)
from git_integrate.integrate import integrate_op
from git_integrate.land import land_op
from git_integrate.schema import RC_NOT_A_GIT_REPO, RC_WORKTREE_MISSING
from universal_concurrency import FifoCapacityGate
from universal_logging import get_logger

from services.git_integration_worker.config import WorkerConfig, load_config
from services.git_integration_worker.models.api import (
    CommitRequest,
    DiffResponse,
    DiffStat,
    DiffStatFile,
    IntegrateRequest,
    IntegrateResponse,
    LandRequest,
    ReachableResponse,
    StatusResponse,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/git", tags=["git-integration"])

# Single-owner serializer: one integrate at a time in this process (1117 S2).
_GATE = FifoCapacityGate(limit=1, gate_id="git-integrate")
_CONFIG: WorkerConfig = load_config()

# A git commit token: 7–40 hex chars. Guards the read-only reachability probe
# against arbitrary subprocess input before it reaches git.
_SHA_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


@asynccontextmanager
async def _integrate_slot() -> AsyncIterator[None]:
    req_id = str(uuid.uuid4())
    await _GATE.acquire(req_id)
    try:
        yield
    finally:
        await _GATE.release()


def _config(request: Request) -> WorkerConfig:
    return getattr(request.app.state, "worker_config", _CONFIG)


def _current_branch(worktree_path: str) -> str:
    """Resolve the worktree's current branch ("" on detached HEAD or failure)."""
    try:
        proc = subprocess.run(
            ["git", "-C", worktree_path, "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=10.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.warning(
            "git branch --show-current failed for %s", worktree_path, exc_info=True
        )
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _parse_numstat(raw: str) -> DiffStat:
    """Parse ``git diff --numstat`` output into a compact DiffStat.

    Each line is ``<insertions>\\t<deletions>\\t<path>``; binary files report
    ``-`` for both counts. Path may carry a rename arrow — preserved verbatim.
    """
    files: list[DiffStatFile] = []
    total_ins = total_del = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        ins_s, del_s, path = parts[0], parts[1], "\t".join(parts[2:])
        binary = not (ins_s.isdigit() and del_s.isdigit())
        ins = int(ins_s) if ins_s.isdigit() else 0
        dels = int(del_s) if del_s.isdigit() else 0
        total_ins += ins
        total_del += dels
        files.append(
            DiffStatFile(path=path, insertions=ins, deletions=dels, binary=binary)
        )
    return DiffStat(
        files_changed=len(files),
        insertions=total_ins,
        deletions=total_del,
        files=files,
    )


def _status_sync(worktree_path: str) -> StatusResponse:
    if not os.path.isdir(worktree_path):
        return StatusResponse(
            worktree_path=worktree_path,
            status="rejected",
            reason_code=RC_WORKTREE_MISSING,
            reason=f"worktree does not exist: {worktree_path!r}",
        )
    try:
        subprocess.run(
            ["git", "-C", worktree_path, "rev-parse", "--git-dir"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return StatusResponse(
            worktree_path=worktree_path,
            status="rejected",
            reason_code=RC_NOT_A_GIT_REPO,
            reason=f"not a git repo: {worktree_path!r}",
        )

    branch = _current_branch(worktree_path)

    status_proc = subprocess.run(
        ["git", "-C", worktree_path, "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=10.0,
    )
    dirty = bool(status_proc.stdout.strip()) if status_proc.returncode == 0 else False

    emit_git_status_read(worktree_path=worktree_path, dirty=dirty, branch=branch)
    return StatusResponse(
        worktree_path=worktree_path,
        branch=branch,
        dirty=dirty,
        status="ok",
    )


def _diff_sync(
    worktree_path: str, path_filter: str, include_full_diff: bool
) -> DiffResponse:
    if not os.path.isdir(worktree_path):
        return DiffResponse(
            worktree_path=worktree_path,
            path_filter=path_filter,
            status="rejected",
            reason_code=RC_WORKTREE_MISSING,
            reason=f"worktree does not exist: {worktree_path!r}",
        )
    try:
        subprocess.run(
            ["git", "-C", worktree_path, "rev-parse", "--git-dir"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return DiffResponse(
            worktree_path=worktree_path,
            path_filter=path_filter,
            status="rejected",
            reason_code=RC_NOT_A_GIT_REPO,
            reason=f"not a git repo: {worktree_path!r}",
        )

    # The fingerprint is always over the full arc-vs-master change set; the
    # diffstat summarizes that same set. The full unified body is the costly
    # part — only materialize it when explicitly requested (friction 11511).
    dirty = is_dirty(worktree_path)
    sha = land_fingerprint(worktree_path)
    diffstat = _parse_numstat(land_diff_numstat(worktree_path))
    branch = _current_branch(worktree_path)
    diff_text = land_diff_text(worktree_path, path_filter) if include_full_diff else ""
    return DiffResponse(
        worktree_path=worktree_path,
        diff=diff_text,
        diff_sha256=sha,
        diffstat=diffstat,
        branch=branch,
        path_filter=path_filter,
        includes_uncommitted=dirty,
        full_diff_included=include_full_diff,
        status="ok",
    )


@router.post(
    "/integrate",
    response_model=IntegrateResponse,
    status_code=200,
    summary="Atomically merge a reviewed arc worktree into master (ref-level CAS).",
)
async def integrate(req: IntegrateRequest, request: Request) -> IntegrateResponse:
    """Serialize integrates via ``FifoCapacityGate(limit=1)`` in this single owner."""
    cfg = _config(request)
    async with _integrate_slot():
        result = await integrate_op(
            arc=req.arc,
            phase=req.phase,
            worktree_path=req.worktree_path,
            approval=req.approval,
            expected_diff_sha256=req.expected_diff_sha256,
            source_repo=str(cfg.source_repo),
            green_gate_cmd=list(cfg.green_gate_cmd),
            remove_worktree=req.remove_worktree,
        )
    return IntegrateResponse(**result)


@router.post(
    "/land",
    response_model=IntegrateResponse,
    status_code=200,
    summary="Atomically commit (if dirty), merge, gate, and land arc into master.",
)
async def land(req: LandRequest, request: Request) -> IntegrateResponse:
    """Serialize land via ``FifoCapacityGate(limit=1)`` — same slot as integrate."""
    cfg = _config(request)
    async with _integrate_slot():
        result = await land_op(
            arc=req.arc,
            phase=req.phase,
            worktree_path=req.worktree_path,
            approval=req.approval,
            expected_diff_sha256=req.expected_diff_sha256,
            commit_message=req.commit_message,
            source_repo=str(cfg.source_repo),
            green_gate_cmd=list(cfg.green_gate_cmd),
            remove_worktree=req.remove_worktree,
        )
    return IntegrateResponse(**result)


@router.post(
    "/commit",
    response_model=IntegrateResponse,
    status_code=200,
    summary="Commit explicit named paths on the current branch (non-arc, gated).",
)
async def commit(req: CommitRequest, request: Request) -> IntegrateResponse:
    """Path-explicit gated commit; serialized via the shared integrate gate.

    ``dry_run=true`` returns the path-scoped fingerprint + numstat for approval
    binding without committing. The fingerprint covers ONLY the named paths;
    the commit isolates to them via ``git commit -- <paths>`` so concurrent
    edits to unnamed files are never captured.
    """
    async with _integrate_slot():
        result = await commit_op(
            worktree_path=req.worktree_path,
            expected_branch=req.expected_branch,
            paths=req.paths,
            approval=req.approval,
            expected_paths_sha256=req.expected_paths_sha256,
            commit_message=req.commit_message,
            dry_run=req.dry_run,
        )
    return IntegrateResponse(**result)


@router.get(
    "/active-work",
    summary="Aggregate in-flight integrate count for drain-aware restart.",
)
async def get_active_work() -> JSONResponse:
    """Return gate occupancy so manage can defer restart during integrate."""
    running = _GATE.active_count
    queued = _GATE.queue_length
    return JSONResponse(
        status_code=200,
        content={
            "running": running,
            "queued": queued,
            "busy": running > 0 or queued > 0,
        },
    )


@router.get("/status", response_model=StatusResponse)
async def status(
    worktree_path: str = Query(..., description="Absolute path to the arc worktree."),
) -> StatusResponse:
    """Read-only status; does not acquire the integrate gate."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _status_sync(worktree_path))


@router.get("/diff", response_model=DiffResponse)
async def diff(
    worktree_path: str = Query(..., description="Absolute path to the arc worktree."),
    path_filter: str = Query(
        "",
        description="Optional pathspec limiting the unified diff (display only; "
        "requires include_full_diff).",
    ),
    include_full_diff: bool = Query(
        True,
        description="Include the full unified diff body. Default true preserves "
        "legacy callers; pass false for compact-only (diff_sha256 + diffstat + "
        "branch + includes_uncommitted).",
    ),
) -> DiffResponse:
    """Read-only diff envelope + ``diff_sha256`` fingerprint.

    Does not acquire the integrate gate. The fingerprint and diffstat always
    describe the full arc-vs-master change set. By default the inline unified
    body is included; pass ``include_full_diff=false`` for compact-only.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: _diff_sync(worktree_path, path_filter, include_full_diff)
    )


@router.get("/reachable", response_model=ReachableResponse)
async def reachable(
    request: Request,
    sha: str = Query(
        ...,
        description="Commit SHA (7–40 hex) to test against local refs/heads/master.",
    ),
) -> ReachableResponse:
    """Read-only: is ``sha`` reachable from LOCAL refs/heads/master in source_repo.

    Does not acquire the integrate gate. ``source_repo`` is the worker-owned
    ``cfg.source_repo`` — the route takes no path argument. Reconciles against
    **local** master only (origin push is separate/operator-owned). Returns
    ``exists`` (rev-parse) and ``reachable`` (merge-base --is-ancestor)
    separately so callers can distinguish a phantom SHA from a real commit not
    yet on master. Backs the cortex landed-claim audit detector.
    """
    cfg = _config(request)
    if not _SHA_TOKEN_RE.fullmatch(sha):
        return ReachableResponse(
            sha=sha,
            status="rejected",
            reason_code="invalid_sha",
            reason="sha must be a 7–40 character hex commit token",
        )
    source_repo = str(cfg.source_repo)
    exists = await commit_exists(source_repo, sha)
    is_reachable = await is_reachable_from_master(source_repo, sha) if exists else False
    return ReachableResponse(sha=sha, exists=exists, reachable=is_reachable)
