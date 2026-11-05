"""Concern aggregate HTTP routes (OpenAPI / MCP binding)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from openapi_mcp.binding import x_mcp

from ..concern_aggregate.checkpoint import write_checkpoint
from ..concern_aggregate.journal import TransitionRejectedError, append_transition
from ..concern_aggregate.recovery import rebuild_concern
from ..db import cortex_conn
from ..models.concern_aggregate import (
    ConcernRebuildRequest,
    ConcernRebuildResponse,
    ConcernTransitionRequest,
    ConcernTransitionResponse,
)

router = APIRouter(prefix="/concern-aggregate", tags=["concern-aggregate"])


@router.post(
    "/transition",
    response_model=ConcernTransitionResponse,
    openapi_extra=x_mcp("concern_transition"),
)
def concern_transition_route(body: ConcernTransitionRequest) -> ConcernTransitionResponse:
    conn = cortex_conn()
    try:
        result = append_transition(
            conn,
            concern_id=body.concern_id,
            kind=body.kind,
            payload=body.payload,
            record_ref=body.record_ref,
            authorized_by=body.authorized_by,
            intent_id=body.intent_id,
            request_id=body.request_id,
            delivery_id=body.delivery_id,
            emit_advisory=body.emit_advisory,
        )
    except TransitionRejectedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        conn.close()
    return ConcernTransitionResponse(**result)


@router.post(
    "/rebuild",
    response_model=ConcernRebuildResponse,
    openapi_extra=x_mcp("concern_rebuild"),
)
def concern_rebuild_route(body: ConcernRebuildRequest) -> ConcernRebuildResponse:
    conn = cortex_conn()
    try:
        if body.checkpoint:
            write_checkpoint(conn, concern_id=body.concern_id)
        state = rebuild_concern(conn, body.concern_id)
    finally:
        conn.close()
    return ConcernRebuildResponse(concern_id=body.concern_id, state=state)
