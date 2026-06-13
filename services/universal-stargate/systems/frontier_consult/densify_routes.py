"""Densify review routes — candidate-ready transition + reconcile closeout."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .admission import FrontierEndpointError
from .densify_candidate_ready import (
    DensifyCandidateReadyBody,
    handle_densify_candidate_ready,
)
from .densify_review_reconcile import parse_densify_review_reconcile
from .events import FrontierDensifyReviewOutcome

densify_router = APIRouter(prefix="/api/v1/team/densify", tags=["team-densify"])


class DensifyReviewReconcileBody(BaseModel):
    model_config = {"extra": "forbid"}

    reconcile: dict[str, Any]


def _publish_event(event: Any) -> None:
    try:
        from systems.proxy.dependencies import get_proxy

        proxy = get_proxy()
        event_bus = getattr(proxy, "event_bus", None)
        if event_bus is not None:
            event_bus.publish_from_sync(event)
    except Exception:
        pass


@densify_router.post("/candidate-ready", status_code=200, response_model=None)
async def densify_candidate_ready(
    body: DensifyCandidateReadyBody,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    request_id = uuid.uuid4().hex[:12]
    try:
        return await handle_densify_candidate_ready(
            request_id=request_id,
            body=body,
            response=response,
            event_publisher=_publish_event,
        )
    except FrontierEndpointError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@densify_router.post("/review-reconcile", status_code=200, response_model=None)
async def densify_review_reconcile(
    body: DensifyReviewReconcileBody,
) -> dict[str, Any] | JSONResponse:
    request_id = uuid.uuid4().hex[:12]
    parsed = parse_densify_review_reconcile(body.reconcile)
    if parsed is None:
        return JSONResponse(
            status_code=422,
            content=FrontierEndpointError(
                request_id=request_id,
                field="reconcile",
                reason="invalid densify_review_reconcile payload",
                status_code=422,
                code="densify_review_reconcile_invalid",
            ).to_dict(),
        )
    _publish_event(
        FrontierDensifyReviewOutcome(
            parent_request_id=parsed["parent_request_id"],
            review_execution_id=parsed["review_execution_id"],
            finding_delta=parsed["finding_delta"],
            reviewer_concur_only=parsed["reviewer_concur_only"],
            folded_finding_ids=parsed["folded_finding_ids"],
        )
    )
    return {"ok": True, **parsed}
