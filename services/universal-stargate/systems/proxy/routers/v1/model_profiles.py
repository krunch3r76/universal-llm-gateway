"""Intelligence profile endpoints — model suitability lookup and query.

GET  /v1/models/{model_id}/profile      — single model profile
POST /v1/models/select                  — unified three-tier cascade selection
POST /v1/models/observe                 — record observation for reputation (full
    payload)
POST /v1/models/profiles/reload         — hot-reload curated YAML profiles
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from intelligence_profiles.requirements import SelectionRequest
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from ....profiles.exclusions import get_excluded_models, load_exclusions
from ....profiles.reputation_scorer import score_record
from ....profiles.selection import select_models as unified_select
from ...dependencies import get_auth_dependency, get_proxy
from ...stargate_core import StargateProxy

logger = logging.getLogger(__name__)

router = APIRouter(tags=["model-profiles"])


class ModelObservationPayload(BaseModel):
    """Request body for POST /v1/models/observe (consult grounding → reputation)."""

    task: str
    model_id: str
    outcome: str
    latency_ms: float
    quality_score: float | None = None
    tokens_per_second: float | None = None


type CloudSelectFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class _CloudSelectable(Protocol):
    def select_models(self, payload: dict[str, Any]) -> Awaitable[dict[str, Any]]: ...


def _get_nested_attr(obj: object, *attrs: str) -> object | None:
    """Safely traverse nested attributes of an object.

    Attempts to access a sequence of attributes on the object. If any attribute
    in the sequence is not found or an intermediate value is None, returns None
    immediately, avoiding AttributeError.

    Args:
        obj: Starting object for attribute traversal.
        *attrs: Attribute names to access in order.

    Returns:
        The value of the last attribute if all steps succeed; otherwise None.
    """
    current: object | None = obj
    for attr in attrs:
        if current is None:
            return None
        current = getattr(current, attr, None)
    return current


@router.get("/models/{model_id:path}/profile")
async def get_model_profile(
    model_id: str,
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Look up the intelligence profile for a single model."""
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


def _get_cloud_select_fn(proxy: StargateProxy) -> CloudSelectFn | None:
    """Extract async cloud select callable from proxy, or None."""
    client = _get_nested_attr(
        proxy,
        "federation_integration",
        "forwarder",
        "cloud_forwarder",
    )
    if not client or not hasattr(client, "select_models"):
        return None
    cloud_client = cast(_CloudSelectable, client)

    return cloud_client.select_models


@router.post("/models/select")
async def select_models_endpoint(
    payload: dict[str, Any],
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Unified model selection — runs the three-tier cascade server-side.

    Selection tiers (highest priority first):
      1. Intelligence profile query (task + score + source + cost + latency)
      2. Cloud proxy tag-based selection (tags + exclude_tags + min_context)
      3. Static defaults (server-owned fallback)

    Returns:
      {"models": [{"id", "source", ...}], "count": int, "selection_path": str}
    """
    try:
        request = SelectionRequest.model_validate(payload)
    except PydanticValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    selection_kwargs: dict[str, Any] = {
        "profile_store": proxy.intelligence_profile_store,
        "cloud_select_fn": _get_cloud_select_fn(proxy),
        "reputation_store": proxy.model_health_store,
        "policy": proxy.reputation_policy,
        "event_bus": proxy.event_bus,
    }

    result = await unified_select(
        request,
        **selection_kwargs,
    )

    return JSONResponse(
        content={
            "models": result.models,
            "count": len(result.models),
            "selection_path": result.selection_path,
        }
    )


@router.post("/models/observe")
async def observe_model(
    payload: ModelObservationPayload,
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Record a model observation (e.g. consult grounding exclusion) for reputation."""
    store = proxy.model_health_store
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="Reputation store not initialized",
        )
    store.observe(
        task=payload.task,
        model_id=payload.model_id,
        latency_ms=payload.latency_ms,
        outcome=payload.outcome,
        quality_score=payload.quality_score,
        tokens_per_second=payload.tokens_per_second,
    )
    return JSONResponse(content={"status": "observed"})


@router.post("/models/profiles/reload")
async def reload_intelligence_profiles(
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Hot-reload curated intelligence profiles from disk.

    Re-reads all YAML files in the intelligence_profiles/ config directory
    and merges them over any existing derived profiles. Existing derived
    profiles are preserved; only the curated layer is replaced.

    Useful after adding or editing curated profile YAML files without restarting.
    """
    store = proxy.intelligence_profile_store
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="Intelligence profile store not initialized",
        )

    curated_dir = _resolve_curated_profile_dir(proxy)
    if curated_dir is None or not curated_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"Curated profile directory not found: {curated_dir}",
        )

    loaded = await asyncio.get_event_loop().run_in_executor(
        None, store.load_curated, curated_dir
    )
    logger.info(
        "Hot-reloaded %d curated intelligence profiles from %s",
        loaded,
        curated_dir,
    )

    return JSONResponse(
        content={
            "loaded": loaded,
            "curated_dir": str(curated_dir),
            "total_profiles": store.count,
        }
    )


def _resolve_curated_profile_dir(proxy: StargateProxy) -> Path | None:
    """Resolve the intelligence_profiles/ directory from proxy config."""
    config = getattr(proxy, "config", None)
    if config is None:
        return None
    config_path = getattr(config, "config_path", None)
    if not config_path:
        return Path("config") / "intelligence_profiles"
    return Path(config_path).parent / "intelligence_profiles"


async def get_model_rankings(
    task: str,
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Get ranked models for a task with reputation scores and exclusion status.

    Args:
        task: Task name used for reputation lookup.
        proxy: Injected Stargate proxy (reputation store, policy).
        _current_user: Injected auth (unused; required for route protection).

    Returns:
        JSONResponse with task, rankings (model_id, score, observations,
        confidence, reliability, quality, excluded), and exclusions list.
    """
    reputation_store = proxy.model_health_store
    if reputation_store is None:
        raise HTTPException(
            status_code=503,
            detail="Reputation store not initialized",
        )

    policy = proxy.reputation_policy
    exclusions = load_exclusions()
    excluded = get_excluded_models(task, exclusions)

    all_records = reputation_store.get_all_for_task(task)

    rankings: list[dict[str, object]] = []
    for model_id, record in sorted(all_records.items()):
        score = score_record(model_id=model_id, record=record, policy=policy)
        rankings.append(
            {
                "model_id": model_id,
                "score": round(score.final_score, 4),
                "observations": round(
                    score.components.confidence * policy.confidence_full_samples,
                ),
                "confidence": round(score.components.confidence, 4),
                "reliability": round(score.components.reliability, 4),
                "quality": round(score.components.quality, 4),
                "excluded": model_id in excluded,
            }
        )

    rankings.sort(key=lambda r: float(r.get("score", 0)), reverse=True)

    return JSONResponse(
        content={
            "task": task,
            "rankings": rankings,
            "exclusions": sorted(excluded),
        }
    )
