"""Models endpoint for local and federated model discovery.

Defines the FastAPI router for `/v1/models`, including source/type filtering
and optional metadata/debug enrichments.
"""

import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from universal_logging import get_logger

from systems.federation import get_federation_integration
from systems.routing.selection.catalog import (
    get_activated_models_for_display,
    get_all_available_models,
    get_local_model_ids,
    get_model_context_metadata,
    get_model_dispatch_metadata,
    get_model_source_map,
    get_model_status_map,
)

from ...dependencies import get_auth_dependency, get_proxy
from ...stargate_core import StargateProxy

logger = get_logger(__name__)
router = APIRouter(tags=["models"])


def _get_pipeline_ids(proxy: StargateProxy) -> list[str]:
    """Get sorted pipeline IDs from registry."""
    if not proxy.is_pipeline_system_ready or not proxy.pipeline_registry:
        return []
    return sorted(proxy.pipeline_registry.pipelines.keys())


def _get_gateway_stats(proxy: StargateProxy) -> dict[str, int]:
    """Get gateway availability statistics."""
    # Router-only Master has no local gateway
    local_gateway_count = 0
    if proxy.gateway_manager:
        local_gateway_count = 1 if proxy.gateway_manager.get_gateway() else 0

    federated_gateway_count = 0
    if proxy.federated_manager:
        federated_gateway_count = len(proxy.federated_manager.get_healthy_gateways())

    return {
        "local": local_gateway_count,
        "federated": federated_gateway_count,
        "total": local_gateway_count + federated_gateway_count,
    }


def _build_models_response(
    model_ids: list[str],
    pipeline_ids: list[str],
    context_metadata: dict[str, dict[str, int]] | None = None,
    dispatch_metadata: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Build OpenAI-compatible models list response.

    When *context_metadata* is provided, each model entry is enriched with
    ``context_length`` and ``effective_context_per_slot`` (where known).

    The ``type`` field distinguishes real inference models ("model") from
    pipeline virtual IDs ("pipeline") so consumers can filter explicitly.
    """
    now = int(time.time())
    data: list[dict] = []

    for model_id in sorted(model_ids):
        entry: dict = {
            "id": model_id,
            "object": "model",
            "type": "model",
            "owned_by": "universal-llm-gateway",
            "permission": ["generate"],
            "created": now,
        }
        if context_metadata:
            meta = context_metadata.get(model_id)
            if meta:
                entry.update(meta)
        if dispatch_metadata:
            dispatch = dispatch_metadata.get(model_id)
            if dispatch:
                entry["dispatch"] = dispatch
        data.append(entry)

    for pipeline_id in sorted(pipeline_ids):
        data.append(
            {
                "id": pipeline_id,
                "object": "model",
                "type": "pipeline",
                "owned_by": "universal-llm-gateway",
                "permission": ["generate"],
                "created": now,
            }
        )

    return {"object": "list", "data": data}


def _build_model_entry(
    model_id: str,
    context_metadata: dict[str, dict[str, int]] | None = None,
    dispatch_metadata: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Build one OpenAI-compatible model object."""
    now = int(time.time())
    entry: dict[str, Any] = {
        "id": model_id,
        "object": "model",
        "type": "model",
        "owned_by": "universal-llm-gateway",
        "permission": ["generate"],
        "created": now,
    }
    if context_metadata:
        meta = context_metadata.get(model_id)
        if meta:
            entry.update(meta)
    if dispatch_metadata:
        dispatch = dispatch_metadata.get(model_id)
        if dispatch:
            entry["dispatch"] = dispatch
    return entry


def _build_debug_info(proxy: StargateProxy, stats: dict[str, int]) -> dict[str, Any]:
    """Build debug info for include_sources=true."""
    federation_integration = get_federation_integration()
    if federation_integration and federation_integration.config:
        local_id = federation_integration.config.stargate_id
    else:
        local_id = "local"

    # Router-only Master has no local gateway_manager
    # get_model_source_map handles None gateway_manager gracefully
    return {
        "_debug_sources": get_model_source_map(
            local_id,
            proxy.gateway_manager,  # May be None for router-only
            proxy.federated_manager,
        ),
        "_debug_stats": {
            "local_gateway": stats["local"],
            "federated_gateways": stats["federated"],
            "total_sources": stats["total"],
        },
    }


@router.get("/models")
async def list_models(
    proxy: StargateProxy = Depends(get_proxy),
    current_user: dict[str, Any] = Depends(get_auth_dependency),
    include_sources: bool = Query(
        False, description="Include source Stargates (debug)"
    ),
    include_metadata: bool = Query(
        False, description="Include context_length per model"
    ),
    type: Literal["model", "pipeline"] | None = Query(
        None,
        description="Filter by entry type: 'model' for inference models only, "
        "'pipeline' for pipeline virtual IDs only",
    ),
    activation: Literal["unfiltered", "filtered"] | None = Query(
        None,
        description="Visibility filter: 'unfiltered' = every known context "
        "(including unpublished), 'filtered' = only published contexts. "
        "Default: 'filtered'",
    ),
    source: Literal["localhost"] | None = Query(
        None,
        description="Source filter: 'localhost' = locally attached gateway only. "
        "Omitted = all reachable sources (local + federated)",
    ),
):
    """
    List available models from Stargate pool.

    Returns published models by default (activation filter applied).
    Unpublished models are still routable if explicitly requested.

    Query parameters for filtering:
    - ``type``: Filter to only models or only pipelines
    - ``activation=unfiltered``: Include unpublished contexts
    - ``source=localhost``: Locally attached gateway only (no federated)
    - ``include_metadata=true``: Enrich with context_length per model
    """
    request_start = time.time()
    show_all = activation == "unfiltered"

    if source == "localhost":
        model_ids_set = get_local_model_ids(proxy.gateway_manager)
    elif show_all:
        model_ids_set = get_all_available_models(
            proxy.gateway_manager,
            proxy.federated_manager,
        )
    else:
        model_ids_set = get_activated_models_for_display(
            proxy.gateway_manager,
            proxy.federated_manager,
        )
    model_ids = sorted(model_ids_set)

    pipeline_ids = _get_pipeline_ids(proxy)
    if pipeline_ids:
        logger.debug("Found %d pipeline(s)", len(pipeline_ids))

    stats = _get_gateway_stats(proxy)

    if stats["total"] == 0:
        logger.warning(
            "No gateways available - /v1/models will return empty list. "
            "Check gateway connectivity and federation config."
        )

    context_metadata = None
    if include_metadata:
        context_metadata = get_model_context_metadata(
            proxy.gateway_manager,
            proxy.federated_manager,
        )

    dispatch_metadata = get_model_dispatch_metadata(
        proxy.gateway_manager,
        proxy.federated_manager,
    )

    # Apply type filter
    models_to_include = model_ids if type != "pipeline" else []
    pipelines_to_include = pipeline_ids if type != "model" else []

    response = _build_models_response(
        models_to_include,
        pipelines_to_include,
        context_metadata,
        dispatch_metadata,
    )

    if include_sources:
        response.update(_build_debug_info(proxy, stats))

    total_duration = time.time() - request_start
    logger.info(
        "/v1/models returned %d entries (%d models, %d pipelines, "
        "type=%s, activation=%s, source=%s) in %.2fs",
        len(response["data"]),
        len(model_ids),
        len(pipeline_ids),
        type or "all",
        activation or "filtered",
        source or "all",
        total_duration,
    )
    return response


@router.get("/models/{model_id}")
async def get_model(
    model_id: str,
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, Any] = Depends(get_auth_dependency),
    activation: Literal["unfiltered", "filtered"] = Query(
        "unfiltered",
        description="Visibility scope: 'unfiltered' checks Stargate's full catalog "
        "(including unpublished contexts), 'filtered' checks only the "
        "published subset",
    ),
    include_metadata: bool = Query(
        False, description="Include context_length per model"
    ),
    include_status: bool = Query(
        False,
        description="Include aggregate load status (loaded/busy/loading/available)",
    ),
) -> dict[str, Any]:
    """Get one model/context by synthetic ID."""
    all_models = get_all_available_models(
        proxy.gateway_manager,
        proxy.federated_manager,
    )
    activated_models = get_activated_models_for_display(
        proxy.gateway_manager,
        proxy.federated_manager,
    )

    search_space = activated_models if activation == "filtered" else all_models
    if model_id not in search_space:
        raise HTTPException(
            status_code=404,
            detail=f"Model not found (activation={activation}): {model_id}",
        )

    context_metadata = None
    if include_metadata:
        context_metadata = get_model_context_metadata(
            proxy.gateway_manager,
            proxy.federated_manager,
        )

    dispatch_metadata = get_model_dispatch_metadata(
        proxy.gateway_manager,
        proxy.federated_manager,
    )

    entry = _build_model_entry(model_id, context_metadata, dispatch_metadata)
    entry["activated"] = model_id in activated_models
    entry["available"] = model_id in all_models

    if include_status:
        fed = get_federation_integration()
        local_id = fed.config.stargate_id if fed and fed.config else "local"
        status_map = get_model_status_map(
            local_id,
            proxy.gateway_manager,
            proxy.federated_manager,
        )
        model_st = status_map.get(model_id, {})
        if model_st.get("busy_on"):
            entry["status"] = "busy"
        elif model_st.get("loading_on"):
            entry["status"] = "loading"
        elif model_st.get("loaded_on"):
            entry["status"] = "loaded"
        else:
            entry["status"] = "available"

    return entry
