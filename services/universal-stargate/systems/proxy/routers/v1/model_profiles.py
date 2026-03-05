"""Intelligence profile endpoints — model suitability lookup and query.

GET  /v1/models/{model_id}/profile  — single model profile
POST /v1/models/query-profiles      — structured model selection by requirements
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from intelligence_profiles import IntelligenceProfileStore, ModelRequirements
from pydantic import ValidationError

from ...dependencies import get_auth_dependency, get_proxy
from ...stargate_core import StargateProxy

router = APIRouter(tags=["model-profiles"])


def _get_store(proxy: StargateProxy) -> IntelligenceProfileStore:
    """Extract the intelligence profile store or raise 503."""
    store = proxy.intelligence_profile_store
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="Intelligence profile store not initialized",
        )
    return store


@router.get("/models/{model_id:path}/profile")
async def get_model_profile(
    model_id: str,
    proxy: StargateProxy = Depends(get_proxy),
    current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Look up the intelligence profile for a single model."""
    del current_user
    store = _get_store(proxy)

    profile = store.get(model_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"No profile for model: {model_id}")

    return JSONResponse(content=profile.model_dump(exclude_none=True))


@router.post("/models/query-profiles")
async def query_profiles(
    payload: dict[str, Any],
    proxy: StargateProxy = Depends(get_proxy),
    current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Query profiles by model requirements.

    Accepts a ModelRequirements-shaped payload and returns ranked model IDs
    with their profiles, filtered by task suitability, cost, context, etc.
    """
    del current_user
    store = _get_store(proxy)

    try:
        requirements = ModelRequirements.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    model_ids = store.query(requirements)

    results: list[dict[str, Any]] = []
    for mid in model_ids:
        profile = store.get(mid)
        if profile is not None:
            entry = profile.model_dump(exclude_none=True)
            entry["id"] = mid
            results.append(entry)

    return JSONResponse(
        content={
            "models": results,
            "count": len(results),
            "query": requirements.model_dump(exclude_none=True),
        }
    )
