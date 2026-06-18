"""Admin routes for git-integration-worker drain control.

Phase 1 (worker-side): the worker is the drain authority. ``begin-drain`` is the
one-shot, idempotent admission-close trigger; ``drain-state`` is the read-only
snapshot Phase-2's manage supervisor consults for its final epoch-check before
SIGTERM. Both are read-/control-plane and are NEVER gated by the drain itself.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel
from universal_logging import get_logger

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/git/admin", tags=["git-admin"])


@router.get("/lease-snapshot", summary="Write-lease holder and queue depth.")
async def lease_snapshot(
    request: Request,
    source_repo: str | None = None,
) -> dict[str, object]:
    """F-3 observability: active write-lease + durable queue depth."""
    cfg = getattr(request.app.state, "worker_config", None)
    repo = source_repo
    if repo is None and cfg is not None:
        repo = str(cfg.source_repo.resolve())
    return CursorDispatchLedger.instance().lease_snapshot(source_repo=repo)


@router.get("/dispatch-status", summary="Status of the latest dispatch on a thread.")
async def dispatch_status(request: Request, thread_id: str) -> dict[str, object]:
    row = CursorDispatchLedger.instance().dispatch_status_by_thread(thread_id=thread_id)
    return row if row is not None else {"thread_id": thread_id, "status": None}


class BeginDrainRequest(BaseModel):
    reason: str
    intent_id: str
    drain_epoch: int
    deadline_s: float | None = None


def _controller(request: Request) -> WorkAdmissionController:
    controller = getattr(request.app.state, "admission_controller", None)
    if controller is None:
        # Lifespan didn't run (some test transports skip it); construct a lazy
        # controller bound to the ledger singleton so the route still functions.
        from services.git_integration_worker.cursor_dispatch_ledger import (
            CursorDispatchLedger,
        )

        controller = WorkAdmissionController(
            ledger=CursorDispatchLedger.instance(),
            worker_id="lazy",
            pid=0,
            worker_started_at="lazy",
        )
        request.app.state.admission_controller = controller
    return controller


@router.post("/begin-drain", summary="Enter the drain epoch (idempotent).")
async def begin_drain(req: BeginDrainRequest, request: Request) -> dict[str, Any]:
    """Close admission and emit ``git_worker.drain.started``. Idempotent on
    ``intent_id``+``drain_epoch``. Returns the drain-state snapshot.
    """
    controller = _controller(request)
    snapshot = controller.begin_drain(
        reason=req.reason,
        intent_id=req.intent_id,
        drain_epoch=req.drain_epoch,
        deadline_s=req.deadline_s,
    )
    return snapshot


@router.get("/drain-state", summary="Drain-state snapshot for the final epoch-check.")
async def drain_state(request: Request) -> dict[str, Any]:
    """Read-only; never gated. Carries worker generation identity
    (``worker_id``/``pid``/``worker_started_at``) so a manage supervisor can
    detect a stale-epoch event across a worker restart.
    """
    controller = _controller(request)
    return controller.drain_state()
