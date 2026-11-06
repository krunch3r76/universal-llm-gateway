"""Concern aggregate HTTP routes (OpenAPI / MCP binding)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from openapi_mcp.binding import x_mcp

from ..concern_aggregate.cas import ConcernCasError
from ..concern_aggregate.checkpoint import write_checkpoint
from ..concern_aggregate.if_match import IfMatchParseError, parse_if_match
from ..concern_aggregate.journal import TransitionRejectedError, append_transition
from ..concern_aggregate.recovery import rebuild_concern
from ..concern_aggregate.views import read_head
from ..db import cortex_conn
from ..models.concern_aggregate import (
    ConcernRebuildRequest,
    ConcernRebuildResponse,
    ConcernTransitionRequest,
    ConcernTransitionResponse,
)

router = APIRouter(prefix="/concern-aggregate", tags=["concern-aggregate"])


def _current_head_revision(conn, concern_id: str) -> int:
    head = read_head(conn, concern_id)
    return int(head["revision"]) if head else 0


def _resolve_expected_revision(
    conn,
    *,
    concern_id: str,
    kind: str,
    body: ConcernTransitionRequest,
    if_match: str | None,
) -> int | None:
    if kind != "position_head":
        return body.expected_revision
    current = _current_head_revision(conn, concern_id)
    if if_match is None or not str(if_match).strip():
        raise HTTPException(status_code=428, detail="If-Match required")
    if if_match is not None:
        try:
            parsed = parse_if_match(if_match, current_revision=current)
        except IfMatchParseError as exc:
            reason = str(exc)
            if reason == "missing":
                raise HTTPException(status_code=428, detail="If-Match required") from exc
            if reason == "malformed":
                raise HTTPException(status_code=400, detail="malformed If-Match") from exc
            if reason in {"weak", "wildcard_on_empty"}:
                head = read_head(conn, concern_id) or {
                    "revision": 0,
                    "record_ref": None,
                    "fencing_generation": 0,
                }
                raise HTTPException(
                    status_code=412,
                    detail={"error": "precondition_failed", "current_head": head},
                ) from exc
            raise HTTPException(status_code=400, detail="malformed If-Match") from exc
        if parsed.wildcard:
            return current
        header_rev = parsed.expected_revision
        if body.expected_revision is not None and body.expected_revision != header_rev:
            raise HTTPException(
                status_code=422,
                detail="If-Match and body expected_revision disagree",
            )
        return header_rev
    raise HTTPException(status_code=428, detail="If-Match required")


@router.post(
    "/transition",
    response_model=ConcernTransitionResponse,
    openapi_extra=x_mcp("concern_transition"),
)
def concern_transition_route(
    body: ConcernTransitionRequest,
    request: Request,
    response: Response,
) -> ConcernTransitionResponse:
    if_match = request.headers.get("If-Match")
    conn = cortex_conn()
    try:
        expected_revision = _resolve_expected_revision(
            conn,
            concern_id=body.concern_id,
            kind=body.kind,
            body=body,
            if_match=if_match,
        )
        if body.kind == "position_head" and not body.write_intent_id:
            raise HTTPException(status_code=422, detail="write_intent_id required")
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
            expected_revision=expected_revision,
            write_intent_id=body.write_intent_id,
            material=body.material if body.kind == "position_head" else None,
        )
    except ConcernCasError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.payload) from exc
    except TransitionRejectedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        conn.close()
    response.headers["ETag"] = f'"{result["revision"]}"'
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
