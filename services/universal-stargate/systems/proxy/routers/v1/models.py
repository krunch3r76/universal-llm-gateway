"""Models endpoint - returns local + federated models."""

import time

from fastapi import APIRouter, Depends, Query
from universal_logging import get_logger

from systems.federation import get_federation_integration
from systems.routing.selection.catalog import (
    get_activated_models_for_display,
    get_model_source_map,
)

from ...dependencies import get_auth_dependency, get_proxy
from ...stargate_core import StargateProxy

logger = get_logger(__name__)
router = APIRouter(tags=["models"])


def _get_pipeline_ids(proxy: StargateProxy) -> list[str]:
    """Get sorted pipeline IDs from registry."""
    if not proxy.pipeline_registry:
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


def _build_models_response(model_ids: list[str], pipeline_ids: list[str]) -> dict:
    """Build OpenAI-compatible models list response."""
    all_ids = sorted(set(model_ids) | set(pipeline_ids))
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "owned_by": "universal-llm-gateway",
                "permission": ["generate"],
                "created": int(time.time()),
            }
            for model_id in all_ids
        ],
    }


def _build_debug_info(proxy: StargateProxy, stats: dict[str, int]) -> dict[str, dict]:
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
    current_user: dict = Depends(get_auth_dependency),
    include_sources: bool = Query(
        False, description="Include source Stargates (debug)"
    ),
):
    """
    List available models from Stargate pool.

    Returns activated models (filtered by activated_contexts).
    Routing can still use non-activated models if explicitly requested.
    """
    request_start = time.time()

    # Collect activated models for public display (respects activated_contexts)
    model_ids_set = get_activated_models_for_display(
        proxy.gateway_manager,
        proxy.federated_manager,
    )
    model_ids = sorted(model_ids_set)

    # Collect pipelines
    pipeline_ids = _get_pipeline_ids(proxy)
    if pipeline_ids:
        logger.debug("Found %d pipeline(s)", len(pipeline_ids))

    # Get gateway stats for logging
    stats = _get_gateway_stats(proxy)

    if stats["total"] == 0:
        logger.warning(
            "No gateways available - /v1/models will return empty list. "
            "Check gateway connectivity and federation config."
        )

    # Build response
    response = _build_models_response(model_ids, pipeline_ids)

    if include_sources:
        response.update(_build_debug_info(proxy, stats))

    total_duration = time.time() - request_start
    logger.info(
        "/v1/models returned %d models (%d activated, %d pipelines) "
        "from %d local + %d federated in %.2fs",
        len(response["data"]),
        len(model_ids),
        len(pipeline_ids),
        stats["local"],
        stats["federated"],
        total_duration,
    )
    return response
