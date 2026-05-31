"""Integrate, status, and diff routes for git-integration-worker."""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from git_integrate.events import emit_git_status_read
from git_integrate.git_cas import is_dirty, land_diff_text, land_fingerprint
from git_integrate.integrate import integrate_op
from git_integrate.land import land_op
from git_integrate.schema import RC_NOT_A_GIT_REPO, RC_WORKTREE_MISSING
from universal_concurrency import FifoCapacityGate
from universal_logging import get_logger

from services.git_integration_worker.config import WorkerConfig, load_config
from services.git_integration_worker.models.api import (
    DiffResponse,
    IntegrateRequest,
    IntegrateResponse,
    LandRequest,
    StatusResponse,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/git", tags=["git-integration"])

# Single-owner serializer: one integrate at a time in this process (1117 S2).
_GATE = FifoCapacityGate(limit=1, gate_id="git-integrate")
_CONFIG: WorkerConfig = load_config()


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

    branch_proc = subprocess.run(
        ["git", "-C", worktree_path, "branch", "--show-current"],
        capture_output=True,
        text=True,
        timeout=10.0,
    )
    branch = branch_proc.stdout.strip() if branch_proc.returncode == 0 else ""

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


def _diff_sync(worktree_path: str, path_filter: str) -> DiffResponse:
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

    dirty = is_dirty(worktree_path)
    diff_text = land_diff_text(worktree_path, path_filter)
    sha = land_fingerprint(worktree_path)
    return DiffResponse(
        worktree_path=worktree_path,
        diff=diff_text,
        diff_sha256=sha,
        path_filter=path_filter,
        includes_uncommitted=dirty,
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
        description="Optional pathspec limiting the unified diff (display only).",
    ),
) -> DiffResponse:
    """Read-only diff + ``diff_sha256`` fingerprint; does not acquire the integrate gate."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: _diff_sync(worktree_path, path_filter)
    )
