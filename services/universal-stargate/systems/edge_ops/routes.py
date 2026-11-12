"""HTTP wrappers over the edge-op service functions (``/api/v1/edge/*``).

Resources, not commands, and every request is edge-initiated: Stargate holds
the lease and directive reads open on the edge's own connection and answers
204 when there is nothing, so the edge re-issues. Stargate opens no connection
to the edge and none to MCP.

Each handler is a wrapper: validate, call one service function, render. The
error envelope is the canonical ``{"error": {code, message}}`` for
``/api/v1/*`` — never ``HTTPException(detail=...)``. Every route refuses with
``edge_ops_disabled`` while ``EDGE_OPS_ENABLED`` is off, which is the default.

Lease-bound POST bodies carry ``lease_id`` in JSON; ``GET …/directives`` takes
``lease_id`` as a query parameter (see ``wire.py``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from . import admission, config, leasing
from .auth import require_edge_identity, require_internal_caller
from .model import (
    CODE_DISABLED,
    CODE_IDENTITY_REFUSED,
    EdgeOpRefusedError,
)
from .runtime import journal_for
from .wire import AdmitBody, EventBody, LeaseBody, ReleaseBody, ResultBody

router = APIRouter(prefix="/edge", tags=["edge-ops"])

_DISABLED_MESSAGE = (
    "Edge ops are disabled on this node. Set EDGE_OPS_ENABLED=true to serve "
    "/api/v1/edge/*."
)


def _error_response(
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    """Build the canonical ``{"error": {code, message}}`` envelope."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _refusal_response(error: EdgeOpRefusedError) -> JSONResponse:
    """Render a service-function refusal in the canonical envelope."""
    return _error_response(error.status_code, error.code, error.message)


def _gate() -> JSONResponse | None:
    """Return the refusal when the substrate is gated off, else ``None``."""
    if config.edge_ops_enabled():
        return None
    return _error_response(503, CODE_DISABLED, _DISABLED_MESSAGE)


@router.post("/ops")
async def admit_op(
    body: AdmitBody,
    request: Request,
    _caller: dict[str, Any] = Depends(require_internal_caller),
) -> JSONResponse:
    """Queue one edge op. Returns 202 with its ``op_id``."""
    refused = _gate()
    if refused is not None:
        return refused
    try:
        op_id = await admission.admit(
            journal_for(request),
            kind=body.kind,
            op=body.op,
            args=body.args,
            idempotency_key=body.idempotency_key,
            deadline_s=body.deadline_s,
        )
    except EdgeOpRefusedError as error:
        return _refusal_response(error)
    return JSONResponse(status_code=202, content={"op_id": op_id})


@router.post("/leases")
async def lease_op(body: LeaseBody, request: Request) -> Response:
    """Long-poll for the next queued op. 204 when the hold elapses empty."""
    refused = _gate()
    if refused is not None:
        return refused
    try:
        edge_id = await require_edge_identity(request)
        if body.edge_id is not None and body.edge_id != edge_id:
            raise EdgeOpRefusedError(
                403,
                CODE_IDENTITY_REFUSED,
                "Body edge_id does not match the authenticated edge identity.",
            )
        grant = await leasing.lease(
            journal_for(request),
            edge_id=edge_id,
            kinds=tuple(body.kinds),
            wait_s=body.wait_s,
        )
    except EdgeOpRefusedError as error:
        return _refusal_response(error)
    if grant is None:
        return Response(status_code=204)
    return JSONResponse(status_code=200, content=grant.to_dict())


@router.get("/ops/{op_id}")
async def get_op(
    op_id: str,
    request: Request,
    _caller: dict[str, Any] = Depends(require_internal_caller),
) -> JSONResponse:
    """Return the op record with its state basis named."""
    refused = _gate()
    if refused is not None:
        return refused
    try:
        projection = await admission.get(journal_for(request), op_id)
    except EdgeOpRefusedError as error:
        return _refusal_response(error)
    return JSONResponse(status_code=200, content=projection)


@router.get("/ops/{op_id}/result")
async def await_op_result(
    op_id: str,
    request: Request,
    wait: float = Query(config.AWAIT_MAX_SECONDS, ge=0.0),
    _caller: dict[str, Any] = Depends(require_internal_caller),
) -> JSONResponse:
    """Wait up to 30 s for a terminal state; ``edge_pending`` on timeout.

    A completed op answers with the handler's dict verbatim plus ``op_id``, so
    a relay result reaches the caller unchanged. Never ``ok: true`` on timeout.
    """
    refused = _gate()
    if refused is not None:
        return refused
    try:
        envelope = await admission.await_result(
            journal_for(request), op_id=op_id, timeout_s=wait
        )
    except EdgeOpRefusedError as error:
        return _refusal_response(error)
    return JSONResponse(status_code=200, content=envelope)


@router.post("/ops/{op_id}/events")
async def post_op_event(
    op_id: str,
    body: EventBody,
    request: Request,
) -> JSONResponse:
    """Record one edge event and renew the lease. ``started`` is the one ack."""
    refused = _gate()
    if refused is not None:
        return refused
    try:
        edge_id = await require_edge_identity(request)
        accepted = await leasing.post_event(
            journal_for(request),
            op_id=op_id,
            edge_id=edge_id,
            lease_id=body.lease_id,
            event_type=body.type,
            payload=body.payload,
        )
    except EdgeOpRefusedError as error:
        return _refusal_response(error)
    return JSONResponse(status_code=200, content=accepted)


@router.post("/ops/{op_id}/result")
async def post_op_result(
    op_id: str,
    body: ResultBody,
    request: Request,
) -> JSONResponse:
    """Store the handler result verbatim and close the op."""
    refused = _gate()
    if refused is not None:
        return refused
    try:
        edge_id = await require_edge_identity(request)
        closed = await leasing.post_result(
            journal_for(request),
            op_id=op_id,
            edge_id=edge_id,
            lease_id=body.lease_id,
            result=body.result,
            outcome=body.outcome,
        )
    except EdgeOpRefusedError as error:
        return _refusal_response(error)
    return JSONResponse(status_code=200, content=closed)


@router.post("/ops/{op_id}/release")
async def release_op(
    op_id: str,
    body: ReleaseBody,
    request: Request,
) -> JSONResponse:
    """Give the lease back without a result."""
    refused = _gate()
    if refused is not None:
        return refused
    try:
        edge_id = await require_edge_identity(request)
        released = await leasing.release(
            journal_for(request),
            op_id=op_id,
            edge_id=edge_id,
            lease_id=body.lease_id,
        )
    except EdgeOpRefusedError as error:
        return _refusal_response(error)
    return JSONResponse(status_code=200, content=released)


@router.post("/ops/{op_id}/expire")
async def expire_op(
    op_id: str,
    body: ReleaseBody,
    request: Request,
) -> JSONResponse:
    """Mark a started op expired after the edge consumed a cancel directive."""
    refused = _gate()
    if refused is not None:
        return refused
    try:
        edge_id = await require_edge_identity(request)
        expired = await leasing.expire(
            journal_for(request),
            op_id=op_id,
            edge_id=edge_id,
            lease_id=body.lease_id,
        )
    except EdgeOpRefusedError as error:
        return _refusal_response(error)
    return JSONResponse(status_code=200, content=expired)


@router.get("/ops/{op_id}/directives")
async def get_op_directives(
    op_id: str,
    request: Request,
    lease_id: str = Query(..., min_length=1),
    after: int = Query(0, ge=0),
    wait: float = Query(config.LONG_POLL_SECONDS, ge=0.0),
) -> Response:
    """Long-poll for server-to-edge directives. 204 when none arrive.

    This is the only path from Stargate to the edge, and the edge opens it.
    Fold-on-touch may enqueue a ``cancel`` directive for a started op past its
    deadline; the edge consumes those on this long-poll (A2 wires the client).
    Only the op's lease holder may attach: another configured edge is refused
    with 409, the same answer an unknown op id gets.
    """
    refused = _gate()
    if refused is not None:
        return refused
    try:
        edge_id = await require_edge_identity(request)
        pending = await leasing.directives(
            journal_for(request),
            op_id=op_id,
            edge_id=edge_id,
            lease_id=lease_id,
            after=after,
            wait_s=wait,
        )
    except EdgeOpRefusedError as error:
        return _refusal_response(error)
    if not pending:
        return Response(status_code=204)
    return JSONResponse(
        status_code=200,
        content={"op_id": op_id, "directives": pending},
    )
