"""Dispatch routes for grokbuild-worker.

Phase A.3 endpoint:
* ``GET /api/v1/grokbuild/dispatches/{dispatch_id}/result`` — fetch the
  canonical envelope for a completed dispatch (delegates to
  ``fetch_result_op``).

Phase B endpoints (async build surface, backed by
:class:`GrokbuildExecutionTracker`):
* ``POST /api/v1/grokbuild/dispatches`` — 202 Accepted, spawn subprocess.
* ``GET  /api/v1/grokbuild/dispatches/{id}`` — poll tracker status.
* ``GET  /api/v1/grokbuild/dispatches/{id}/events`` — SSE stream.
* ``DELETE /api/v1/grokbuild/dispatches/{id}`` — cancel (SIGTERM/SIGKILL).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from grokbuild.fetch_result import fetch_result_op
from pydantic import BaseModel

from services.grokbuild_worker.error_map import raise_if_error
from services.grokbuild_worker.events import (
    GrokbuildDispatchFetched,
    GrokbuildDispatchRejectedEvent,
    envelope_outcome,
    publish_nowait,
)
from services.grokbuild_worker.models.async_dispatch import (
    GrokbuildDispatchAccepted,
    GrokbuildDispatchCancelled,
    GrokbuildDispatchRequest,
    GrokbuildDispatchStatus,
)
from services.grokbuild_worker.models.sync import ResultFormat
from services.grokbuild_worker.tracker import (
    GrokbuildExecutionTracker,
    TrackerCapacityError,
)

router = APIRouter(prefix="/api/v1/grokbuild", tags=["grokbuild-dispatches"])


class _ErrorDetail(BaseModel):
    """Structured detail payload accompanying HTTP 4xx/5xx responses."""

    reason_code: str
    reason: str


class _CapacityExhaustedError(BaseModel):
    """HTTP 429 body shape for ``POST /dispatches`` under capacity pressure."""

    reason_code: str = "capacity_exhausted"
    reason: str
    running: int
    capacity: int


class _DispatchErrorResponse(BaseModel):
    """Generic HTTPException-wrapped error envelope used by status/SSE/cancel."""

    detail: _ErrorDetail


def _get_tracker(request: Request) -> GrokbuildExecutionTracker:
    tracker = getattr(request.app.state, "grokbuild_tracker", None)
    if tracker is None:
        raise HTTPException(
            status_code=503,
            detail={
                "reason_code": "tracker_unavailable",
                "reason": "grokbuild tracker is not initialized",
            },
        )
    return tracker


@router.get(
    "/dispatches/{dispatch_id}/result",
    summary="Fetch the result envelope for a completed dispatch.",
    responses={
        404: {"model": _DispatchErrorResponse},
        410: {"model": _DispatchErrorResponse},
        422: {"model": _DispatchErrorResponse},
        500: {"model": _DispatchErrorResponse},
        502: {"model": _DispatchErrorResponse},
    },
)
async def get_dispatch_result(
    dispatch_id: str,
    format: ResultFormat = Query(
        "json", description="Result format: json, text, or summary."
    ),
) -> JSONResponse:
    """Fetch the completed result for a dispatch by ID."""
    t0 = time.monotonic()
    envelope = await fetch_result_op(dispatch_id, format=format)
    raise_if_error(envelope)
    duration_s = time.monotonic() - t0
    result_size = len(
        str(envelope.get("stdout", "") or envelope.get("text", "") or "").encode()
    )
    publish_nowait(
        GrokbuildDispatchFetched(
            dispatch_id=dispatch_id,
            outcome=envelope_outcome(envelope),
            duration_s=duration_s,
            result_size_bytes=result_size,
        )
    )
    return JSONResponse(content=envelope)


@router.post(
    "/dispatches",
    status_code=202,
    response_model=GrokbuildDispatchAccepted,
    summary="Admit an async build dispatch (returns 202 with dispatch_id).",
    responses={
        429: {"model": _CapacityExhaustedError, "description": "Capacity exhausted"},
        503: {"model": _DispatchErrorResponse, "description": "Tracker unavailable"},
    },
)
async def start_dispatch(
    req: GrokbuildDispatchRequest,
    request: Request,
    response: Response,
) -> GrokbuildDispatchAccepted | JSONResponse:
    """Admit an async build dispatch; return 202 with the dispatch_id.

    Capacity exhaustion (operator answer 1c: cap=4) maps to HTTP 429 with
    a ``Retry-After`` hint instead of queueing — callers must back off
    explicitly. The rejection emits a ``grokbuild.dispatch.rejected``
    admission-phase event so the observability path stays uniform with
    other rejection codes (e.g. ``mcp.grokbuild.dispatch.rejected`` on
    the lib side).
    """
    tracker = _get_tracker(request)
    try:
        dispatch_id = await tracker.start(req)
    except TrackerCapacityError as exc:
        # Mint a placeholder dispatch_id so log/event consumers can still
        # correlate this rejection back to a specific HTTP request. The
        # rejection never enters the tracker, so the uuid never collides
        # with a real running dispatch.
        rejection_id = str(uuid.uuid4())
        publish_nowait(
            GrokbuildDispatchRejectedEvent(
                dispatch_id=rejection_id,
                reason_code="capacity_exhausted",
                reason=str(exc),
                running=exc.running,
                capacity=exc.capacity,
            )
        )
        return JSONResponse(
            status_code=429,
            content={
                "reason_code": "capacity_exhausted",
                "reason": str(exc),
                "running": exc.running,
                "capacity": exc.capacity,
            },
            headers={"Retry-After": "30"},
        )
    location = f"/api/v1/grokbuild/dispatches/{dispatch_id}"
    response.headers["Location"] = location
    return GrokbuildDispatchAccepted(
        dispatch_id=dispatch_id,
        status_url=location,
        events_url=f"{location}/events",
        state="pending",
    )


@router.get(
    "/dispatches/{dispatch_id}",
    response_model=GrokbuildDispatchStatus,
    summary="Return tracker snapshot for a dispatch.",
    responses={
        404: {"model": _DispatchErrorResponse, "description": "Unknown or TTL-expired"},
        503: {"model": _DispatchErrorResponse, "description": "Tracker unavailable"},
    },
)
async def get_dispatch_status(
    dispatch_id: str,
    request: Request,
) -> GrokbuildDispatchStatus:
    """Return tracker snapshot; 404 once TTL has expired (24h after terminal)."""
    tracker = _get_tracker(request)
    snapshot = await tracker.status(dispatch_id)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail={
                "reason_code": "dispatch_not_found",
                "reason": (f"unknown or TTL-expired dispatch_id {dispatch_id!r}"),
            },
        )
    return GrokbuildDispatchStatus(**snapshot)


@router.get(
    "/dispatches/{dispatch_id}/events",
    summary="Server-Sent Events stream of tracker progress.",
    responses={
        404: {"model": _DispatchErrorResponse},
        503: {"model": _DispatchErrorResponse},
    },
)
async def stream_dispatch_events(
    dispatch_id: str,
    request: Request,
) -> StreamingResponse:
    """SSE stream of tracker events; closes on terminal state.

    Per the V2 plan contract: client disconnect does NOT cancel the
    subprocess. The tracker drops the listener queue from its fanout set
    but the dispatch task keeps running until completion (or DELETE).
    """
    tracker = _get_tracker(request)
    if await tracker.status(dispatch_id) is None:
        raise HTTPException(
            status_code=404,
            detail={
                "reason_code": "dispatch_not_found",
                "reason": f"unknown or TTL-expired dispatch_id {dispatch_id!r}",
            },
        )

    async def _gen() -> AsyncIterator[bytes]:
        async for event in tracker.stream_events(dispatch_id):
            event_type = str(event.get("type", "message"))
            data = json.dumps(event, default=str)
            yield f"event: {event_type}\ndata: {data}\n\n".encode()

    return StreamingResponse(_gen(), media_type="text/event-stream")


@router.delete(
    "/dispatches/{dispatch_id}",
    response_model=GrokbuildDispatchCancelled,
    summary="Cancel an in-flight dispatch (SIGTERM → 30s → SIGKILL).",
    responses={
        404: {"model": _DispatchErrorResponse, "description": "Unknown dispatch_id"},
        409: {"model": _DispatchErrorResponse, "description": "Already terminal"},
        503: {"model": _DispatchErrorResponse, "description": "Tracker unavailable"},
    },
)
async def cancel_dispatch(
    dispatch_id: str,
    request: Request,
) -> JSONResponse:
    """Operator cancel: SIGTERM → 30s grace → SIGKILL."""
    tracker = _get_tracker(request)
    status_code, body = await tracker.cancel(dispatch_id)
    return JSONResponse(status_code=status_code, content=body)
