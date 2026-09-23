"""Admin routes for git-integration-worker drain control.

The worker is the drain authority. ``begin-drain`` is the one-shot, idempotent
admission-close trigger; ``cancel-drain`` clears ``_draining`` for a matching
``(intent_id, drain_epoch)`` without SIGTERM (manage cancel pairing);
``drain-state`` is the read-only snapshot the manage supervisor consults for its
final epoch-check before SIGTERM. All three are read-/control-plane and are
NEVER gated by the drain itself.
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
async def dispatch_status(
    request: Request, thread_id: str | None = None, dispatch_id: str | None = None
) -> dict[str, object]:
    """Latest row on ``thread_id`` (or the named ``dispatch_id``) plus its ``park`` block.

    ``park`` is ``None`` for rows never parked; otherwise
    ``{state ∈ parked|resumed|expired, intent_id, parked_at, park_resumed_by, …}``
    (steer-restart D2 projection).
    """
    from services.git_integration_worker.cursor_sdk_park_ledger import (
        load_park_row,
        park_projection,
    )

    ledger = CursorDispatchLedger.instance()
    if dispatch_id:
        row = ledger.dispatch_status_by_id(dispatch_id=dispatch_id)
    elif thread_id:
        row = ledger.dispatch_status_by_thread(thread_id=thread_id)
    else:
        return {"thread_id": None, "dispatch_id": None, "status": None, "park": None}
    if row is None:
        return {
            "thread_id": thread_id,
            "dispatch_id": dispatch_id,
            "status": None,
            "park": None,
        }
    row["park"] = park_projection(load_park_row(dispatch_id=str(row["dispatch_id"])))
    return row


class BeginDrainRequest(BaseModel):
    """Request body for ``POST .../begin-drain``.

    Carries the manage restart-intent identity and target drain epoch so the
    worker can enter an idempotent drain generation.
    """

    reason: str
    intent_id: str
    drain_epoch: int
    deadline_s: float | None = None


class CseHolderUpsertRequest(BaseModel):
    """Body for idempotent CSE holder registration upsert."""

    chat_url: str
    registration_id: str | None = None
    execution_id: str | None = None
    lane_thread_id: str | None = None
    work_key: str | None = None
    score_journal_uri: str | None = None


class CseHolderReleaseRequest(BaseModel):
    """Body for releasing a holder row after registry detach."""

    chat_url: str
    registration_id: str | None = None
    reason: str | None = None


@router.post("/cse-holder/release", summary="Release a CSE session holder after detach.")
async def cse_holder_release(req: CseHolderReleaseRequest) -> dict[str, object]:
    from services.git_integration_worker.cse_session_holders import (
        release_holder_for_registration,
    )

    with CursorDispatchLedger.instance()._connect() as conn:
        row = release_holder_for_registration(
            conn,
            chat_url=req.chat_url,
            registration_id=req.registration_id,
            reason=req.reason,
        )
        conn.commit()
    return {"ok": True, "holder": row}


@router.post("/cse-holder/upsert", summary="Register or refresh a CSE session holder.")
async def cse_holder_upsert(req: CseHolderUpsertRequest) -> dict[str, object]:
    """Idempotent upsert keyed by ``holder_id`` from ``chat_url``."""
    from services.git_integration_worker.cse_session_holders import upsert_holder

    with CursorDispatchLedger.instance()._connect() as conn:
        row = upsert_holder(
            conn,
            chat_url=req.chat_url,
            registration_id=req.registration_id,
            execution_id=req.execution_id,
            lane_thread_id=req.lane_thread_id,
            work_key=req.work_key,
            score_journal_uri=req.score_journal_uri,
        )
        conn.commit()
    return {"ok": True, "holder": row}


class CancelDrainRequest(BaseModel):
    """Request body for ``POST .../cancel-drain``.

    Identifies the ``(intent_id, drain_epoch)`` pair that must match before
    ``release_drain`` clears the worker drain flag without SIGTERM.
    """

    intent_id: str
    drain_epoch: int


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


@router.post(
    "/cancel-drain",
    summary="Release drain without SIGTERM (idempotent on intent+epoch).",
)
async def cancel_drain(req: CancelDrainRequest, request: Request) -> dict[str, Any]:
    """Clear ``_draining`` when the body matches the active drain generation.

    Mismatch / generation-gone is an idempotent no-op returning current
    ``drain_state``. Manage ``cancel_restart_intent`` calls this before store
    cancel when a drain epoch is set (release-then-cancel).
    """
    controller = _controller(request)
    return controller.release_drain(
        intent_id=req.intent_id, drain_epoch=req.drain_epoch
    )
