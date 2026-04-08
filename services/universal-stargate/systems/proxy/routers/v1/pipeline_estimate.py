"""Pipeline estimation endpoint for token-budget planning."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from systems.pipeline.core.estimation import (
    EstimateItem,
    compute_code_review_validate_tokens,
    estimate_tokens,
    pack_first_fit_decreasing,
)

from ...dependencies import get_auth_dependency, get_proxy
from ...stargate_core import StargateProxy

router = APIRouter(tags=["pipeline-estimate"])


class EstimateInputItem(BaseModel):
    """Single item for pipeline estimation."""

    name: str
    chars: int = Field(ge=0)


class EstimateRequest(BaseModel):
    """Batching estimate request."""

    pipeline: str
    items: list[EstimateInputItem]


@router.post("/pipelines/estimate")
async def estimate_pipeline_batches(
    payload: EstimateRequest,
    proxy: StargateProxy = Depends(get_proxy),
    current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Estimate source token usage and batches for a pipeline."""
    del current_user  # auth dependency side effect only

    if not proxy.is_pipeline_system_ready or proxy.pipeline_registry is None:
        raise HTTPException(status_code=503, detail="Pipeline execution unavailable")

    try:
        pipeline = proxy.pipeline_registry.get_pipeline(payload.pipeline)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    estimation = (pipeline.model_extra or {}).get("estimation") or {}
    if not estimation:
        raise HTTPException(
            status_code=400,
            detail="Pipeline missing estimation config",
        )

    budget = int(estimation.get("budget_source_tokens", 12000))
    chars_per_token = float(estimation.get("chars_per_token", 3.5))
    warning_threshold = int(estimation.get("large_file_warning_tokens", 20000))
    validate_amplification = float(estimation.get("validate_amplification", 1.3))
    fixed_overhead_tokens = int(estimation.get("fixed_overhead_tokens", 1300))

    items = [
        EstimateItem(
            name=item.name,
            chars=item.chars,
            tokens=estimate_tokens(item.chars, chars_per_token=chars_per_token),
        )
        for item in payload.items
    ]
    batches = pack_first_fit_decreasing(items, budget_tokens=budget)
    total_source_tokens = sum(item.tokens for item in items)
    estimated_validate_tokens = compute_code_review_validate_tokens(
        total_source_tokens,
        validate_amplification=validate_amplification,
        fixed_overhead_tokens=fixed_overhead_tokens,
    )
    warnings = [
        {
            "name": item.name,
            "code": "large_file",
            "message": (
                f"{item.tokens} estimated tokens exceeds warning threshold "
                f"{warning_threshold}"
            ),
        }
        for item in items
        if item.tokens >= warning_threshold
    ]

    return JSONResponse(
        {
            "pipeline": payload.pipeline,
            "budget_tokens": budget,
            "total_source_tokens": total_source_tokens,
            "estimated_validate_tokens": estimated_validate_tokens,
            "items": [asdict(item) for item in items],
            "batches": batches,
            "warnings": warnings,
        }
    )
