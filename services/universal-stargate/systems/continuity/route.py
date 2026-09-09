"""Stargate ``/api/v1/continuity/*`` routes (Phase 0: Door 1 tape-read only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from universal_logging import get_logger

from systems.proxy.dependencies import get_auth_dependency

from .models import TapeReadEnvelopeResponse, TapeReadRequest
from .tape_read import fetch_tape_envelope

logger = get_logger(__name__)
router = APIRouter(prefix="/continuity", tags=["continuity"])


@router.post(
    "/tape-read",
    response_model=TapeReadEnvelopeResponse,
    responses={
        403: {"description": "Not a root continuity lane"},
        503: {"description": "agent-bus unreachable"},
    },
)
async def continuity_tape_read(
    body: TapeReadRequest,
    current_user: dict[str, Any] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Door 1 sync relay — agent-bus tape render wrapped as ``ContinuityMessagesEnvelope``."""
    caller_agent = str(current_user.get("agent") or current_user.get("sub") or "stargate")
    payload, status = await fetch_tape_envelope(
        body.thread,
        scope=body.scope,
        include_extras=body.include_extras,
        tools=body.tools,
        budget_bytes=body.budget_bytes,
        harvest=body.harvest,
        caller_agent=caller_agent,
        door="sync",
    )
    return JSONResponse(content=payload, status_code=status)


__all__ = ["router"]
