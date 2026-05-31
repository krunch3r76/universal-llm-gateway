"""Agent endpoint to report bad models for reputation (e.g. path hallucination)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...dependencies import get_auth_dependency, get_proxy
from ...stargate_core import StargateProxy

router = APIRouter(tags=["report-model"])


class ReportModelPayload(BaseModel):
    """Request body for POST /api/v1/report-model (agents report bad models →"
    "reputation)."""

    task: str
    model_id: str
    reason: str
    details: dict[str, Any] | None = None


@router.post("/report-model")
async def report_model(
    payload: ReportModelPayload,
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Report a bad model (e.g. path hallucination) for reputation. Agents use this to lower
    a model's reputation so selection prefers others. Feeds the same store as POST /v1/models/observe.
    """
    store = proxy.model_health_store
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="Reputation store not initialized",
        )
    store.observe(
        task=payload.task,
        model_id=payload.model_id,
        latency_ms=0.0,
        outcome=payload.reason,
        quality_score=0.0,
        tokens_per_second=None,
    )
    return JSONResponse(content={"status": "reported"})
