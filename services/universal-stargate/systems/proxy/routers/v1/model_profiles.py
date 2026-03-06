"""Intelligence profile endpoints — model suitability lookup and query.

GET  /v1/models/{model_id}/profile  — single model profile
POST /v1/models/select              — unified three-tier cascade selection
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from intelligence_profiles.requirements import SelectionRequest
from pydantic import ValidationError as PydanticValidationError

from ....profiles.selection import select_models as unified_select
from ...dependencies import get_auth_dependency, get_proxy
from ...stargate_core import StargateProxy

router = APIRouter(tags=["model-profiles"])


@router.get("/models/{model_id:path}/profile")
async def get_model_profile(
    model_id: str,
    proxy: StargateProxy = Depends(get_proxy),
    current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Look up the intelligence profile for a single model."""
    del current_user
    store = proxy.intelligence_profile_store
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="Intelligence profile store not initialized",
        )

    profile = store.get(model_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"No profile for model: {model_id}")

    return JSONResponse(content=profile.model_dump(exclude_none=True))


def _get_cloud_select_fn(proxy: StargateProxy) -> Any | None:
    """Extract async cloud select callable from proxy, or None."""
    fed = getattr(proxy, "federation_integration", None)
    if not fed:
        return None
    fwd = getattr(fed, "forwarder", None)
    if not fwd:
        return None
    client = getattr(fwd, "cloud_forwarder", None)
    if not client or not hasattr(client, "select_models"):
        return None

    async def _select(payload: dict[str, Any]) -> dict[str, Any]:
        return await client.select_models(payload)

    return _select


@router.post("/models/select")
async def select_models_endpoint(
    payload: dict[str, Any],
    proxy: StargateProxy = Depends(get_proxy),
    current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Unified model selection — runs the three-tier cascade server-side.

    Selection tiers (highest priority first):
      1. Intelligence profile query (task + score + source + cost + latency)
      2. Cloud proxy tag-based selection (tags + exclude_tags + min_context)
      3. Empty list (client handles static fallback)

    Returns:
      {"models": [{"id", "source", ...}], "count": int, "selection_path": str}
    """
    del current_user

    try:
        request = SelectionRequest.model_validate(payload)
    except PydanticValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    result = await unified_select(
        request,
        profile_store=proxy.intelligence_profile_store,
        cloud_select_fn=_get_cloud_select_fn(proxy),
    )

    return JSONResponse(
        content={
            "models": result.models,
            "count": len(result.models),
            "selection_path": result.selection_path,
        }
    )
