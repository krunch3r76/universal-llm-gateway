"""
Collects Gateway snapshots from live GatewayInstance objects.

Bridges the selection system to the existing gateway infrastructure.
Uses typed ModelMetadata configuration for resource requirements.
Falls back to WebSocket cache for real-time state.

Health/Latency Contract:
    - health_score: float 0.0-1.0 (1.0 = fully healthy)
    - avg_latency_ms: float >= 0.0 (typical range 0-500ms)
    - active_requests: int >= 0 (current inflight requests)
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from model_id import ModelId
from universal_logging import get_logger

if TYPE_CHECKING:
    from .types import Gateway, Placement

logger = get_logger(__name__)

# Timeout for individual gateway status fetch (prevents blocking)
GATEWAY_STATUS_TIMEOUT_S = 2.0


def _validate_model_requirements(
    vram_usage: int | None,
    ram_usage: int | None,
    model_id_str: str,
) -> tuple[int, int] | None:
    """
    Validate model resource requirements for routing inclusion.

    Applies fail-closed policy: models without requirements are excluded
    from routing to prevent incorrect feasibility decisions.

    Args:
        vram_usage: VRAM requirement in MB (None if unknown)
        ram_usage: RAM requirement in MB (None if unknown)
        model_id_str: Model identifier for logging

    Returns:
        (vram_mb, ram_mb) if valid, None if requirements missing

    Invariant: ∀ return (v, r): (v ∈ ℤ≥0 ∧ r ∈ ℤ≥0) ∧ (v > 0 ∨ r > 0)
    """
    if vram_usage is None and ram_usage is None:
        # No requirements available - exclude from routing
        logger.warning(
            f"No resource requirements for loaded model {model_id_str}, "
            f"excluding from model_details"
        )
        return None

    # At least one requirement present (may have 0 for CPU-only models)
    return (vram_usage or 0, ram_usage or 0)


async def collect_gateways(
    gateway_instances: list[Any],  # list[GatewayInstance]
    get_status: callable = None,
    timeout_s: float = GATEWAY_STATUS_TIMEOUT_S,
    include_model_details: bool = False,
    gateway_manager: Any = None,  # For fetching model metadata
) -> list[Gateway]:
    """
    Build Gateway snapshots from live GatewayInstance objects.

    Uses parallel collection with timeout to prevent latency accumulation.
    Slow/failing gateways are skipped gracefully.

    Args:
        gateway_instances: Live gateway objects from SingleGatewayManager
        get_status: Optional async function to get gateway status
                   Default: gateway.client.get_resource_status()
        timeout_s: Timeout per gateway status fetch (default 2s)
        include_model_details: DEPRECATED - always builds model_details from
                              WebSocket cache + catalog (no HTTP fetch)

    Returns:
        List of Gateway snapshots for selection (excludes failed/slow gateways)
    """
    import time

    from .types import Gateway

    if not gateway_instances:
        return []

    snapshot_time = time.time()
    start_time = time.perf_counter()

    async def fetch_one(gw_instance: Any) -> Gateway | None:
        """
        Fetch status for single gateway with timeout.

        Uses WebSocket-only for real-time state (no HTTP fetch).
        Populates model_details from WebSocket cache + local catalog.
        """
        try:
            if get_status:
                status = await asyncio.wait_for(
                    get_status(gw_instance), timeout=timeout_s
                )
            else:
                # WebSocket-only: real-time state (no HTTP fetch)
                status = gw_instance.client.get_resource_status()

            if status is None:
                logger.warning(
                    f"Gateway {gw_instance.config.name} WebSocket disconnected"
                )
                return None

            # Build model_details from cached event data
            # WebSocket state already has vram/ram usage from MODEL_LOADED events
            model_details: dict[ModelId, dict[str, Any]] = {}
            cached_inference = getattr(status, "model_last_inference", {})
            cached_model_details = getattr(status, "model_details", {})

            # MUST include all loaded models even if last_inference_time missing
            for model_id_str in status.loaded_models:
                model_id = ModelId.parse(model_id_str)  # CHANGED: parse to ModelId
                last_inf = cached_inference.get(model_id_str)

                # Get resource usage from WebSocket state (MODEL_LOADED events)
                # This avoids expensive HTTP calls to gateway
                cached_details = cached_model_details.get(model_id_str, {})
                vram_usage = cached_details.get("vram_usage")
                ram_usage = cached_details.get("ram_usage")

                # Fallback to HTTP configuration fetch only if WebSocket cache is empty
                # (e.g., models loaded before Stargate connected)
                if (vram_usage is None or ram_usage is None) and gateway_manager:
                    try:
                        model_config = await gateway_manager.fetch_model_configuration(
                            model_id
                        )
                        if model_config:
                            vram_usage = model_config.vram_usage
                            ram_usage = model_config.ram_usage
                    except Exception as e:
                        logger.warning(
                            f"Failed to fetch configuration for {model_id_str}: {e}",
                            extra={"model_id": model_id_str, "error": str(e)},
                        )

                # Validate requirements (fail-closed policy)
                requirements = _validate_model_requirements(
                    vram_usage, ram_usage, model_id_str
                )
                if requirements is None:
                    continue  # Skip adding to model_details

                vram_usage, ram_usage = requirements
                model_details[model_id] = {  # CHANGED: key is ModelId
                    "last_inference_time": last_inf,
                    "vram_usage": vram_usage,
                    "ram_usage": ram_usage,
                    "status": (
                        "busy" if model_id_str in status.busy_models else "loaded"
                    ),
                }

            # Get available models from WebSocket cache (instant, no HTTP)
            available_models = gw_instance.client.get_models()

            # Parse measured VRAM from WebSocket state (populated by RESOURCE_UPDATE)
            client_state = getattr(gw_instance.client, "state", None)
            measured_vram_raw: dict[str, int] = (
                getattr(client_state, "measured_model_vram", {}) if client_state else {}
            )
            model_measured_vram: dict[ModelId, int] = {}
            for mid_str, vram_mb in measured_vram_raw.items():
                try:
                    model_measured_vram[ModelId.parse(mid_str)] = vram_mb
                except Exception as e:
                    logger.warning(
                        f"Failed to parse measured VRAM model ID {mid_str!r}: {e}"
                    )

            # Get active_requests from in-flight tracker (tracks in-flight requests)
            # WebSocket telemetry doesn't track this, so we query the tracker directly
            active_requests = 0
            try:
                from src.core.gateway.in_flight_requests import in_flight_tracker

                active_requests = in_flight_tracker.get_in_flight_count(
                    gw_instance.config.name
                )
            except Exception as e:
                logger.debug(
                    f"Failed to get active_requests for "
                    f"{gw_instance.config.name}: {e}, defaulting to 0"
                )

            return Gateway(
                ref=gw_instance,
                name=gw_instance.config.name,
                ram_free_mb=status.available_ram_mb,
                vram_free_mb=status.available_vram_mb,
                ram_total_mb=getattr(status, "total_ram_mb", 0),
                vram_total_mb=getattr(status, "total_vram_mb", 0),
                loaded_models=frozenset(
                    ModelId.parse(mid) for mid in status.loaded_models
                ),
                busy_models=frozenset(ModelId.parse(mid) for mid in status.busy_models),
                loading_models=frozenset(
                    ModelId.parse(mid)
                    for mid in gw_instance.client.get_loading_models()
                ),
                available_models=frozenset(
                    ModelId.parse(mid) for mid in available_models
                ),
                model_details=model_details,
                model_measured_vram=model_measured_vram,
                health_score=getattr(status, "health_score", 1.0),
                avg_latency_ms=getattr(status, "avg_latency_ms", 0.0),
                active_requests=active_requests,
                telemetry_timestamp=snapshot_time,
                last_heartbeat=getattr(gw_instance.client, "last_heartbeat_time", 0.0)
                or 0.0,
            )
        except TimeoutError:
            logger.warning(
                f"Gateway {gw_instance.config.name} status timeout ({timeout_s}s)"
            )
            return None
        except Exception as e:
            logger.warning(f"Failed to collect gateway {gw_instance.config.name}: {e}")
            return None

    # Parallel fetch all gateway statuses
    results = await asyncio.gather(
        *(fetch_one(gw) for gw in gateway_instances),
        return_exceptions=True,
    )

    # Filter successful results
    gateways = [r for r in results if isinstance(r, Gateway)]

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    logger.debug(
        f"collect_gateways: {len(gateways)}/{len(gateway_instances)} gateways "
        f"in {elapsed_ms:.1f}ms (no /api/v1/status/resources)"
    )
    return gateways


async def build_placement(
    model_id: ModelId,
    gateway_manager: Any,  # SingleGatewayManager
    original_model_id: str | None = None,
    endpoint_category: str = "generation",
) -> Placement | None:
    """
    Build Placement from model metadata.

    Tries local gateway first, then checks federated gateways for resource requirements.

    Args:
        model_id: Parsed ModelId for routing (routing key for coordination)
        gateway_manager: Manager with fetch_model_configuration() and _federated_manager
        original_model_id: Original model ID from request (with context suffix)
        endpoint_category: "generation" or "embedding" for worker limits

    Returns:
        Placement object or None if model info unavailable
    """
    from .types import Placement

    try:
        # PHASE 2 Investigation: Entry point logging
        logger.info(
            f"🏗️ build_placement() ENTRY: model_id={model_id}, "
            f"original={original_model_id}, is_cpu={model_id.is_cpu}, "
            f"routing_key={model_id.routing_key}"
        )

        # Pass ModelId object directly - method handles appropriate lookup internally
        logger.debug(f"🔍 build_placement: {model_id} (is_cpu={model_id.is_cpu})")

        model_config = await gateway_manager.fetch_model_configuration(model_id)

        # PHASE 2 FIX: If not found locally, try federated gateways
        if not model_config:
            logger.info(
                f"🔍 build_placement: Model {model_id} not found in local gateway, "
                f"checking federated gateways..."
            )

            # Try to get resource requirements from federated gateways
            federated_manager = getattr(gateway_manager, "_federated_manager", None)
            if federated_manager:
                healthy_gateways = federated_manager.get_healthy_gateways()
                logger.debug(
                    f"🔍 build_placement: Checking {len(healthy_gateways)} "
                    f"federated gateways for {model_id}"
                )

                for fed_gw in healthy_gateways:
                    logger.debug(
                        f"🔍 build_placement: Gateway {fed_gw.gateway_id} has "
                        f"{len(fed_gw.model_resources)} models in model_resources"
                    )

                    if model_id in fed_gw.model_resources:
                        resources = fed_gw.model_resources[model_id]
                        vram_mb = resources.get("vram_usage")
                        ram_mb = resources.get("ram_usage")

                        if vram_mb is not None and ram_mb is not None:
                            logger.info(
                                f"✅ build_placement: Found {model_id} resources in "
                                f"federated gateway {fed_gw.gateway_id}: "
                                f"vram={vram_mb}MB, ram={ram_mb}MB"
                            )

                            is_gpu = vram_mb > 0
                            placement = Placement(
                                model_id=model_id,
                                ram_mb=ram_mb,
                                vram_mb=vram_mb,
                                is_gpu=is_gpu,
                                original_model_id=original_model_id,
                                context_length=resources.get("context_length"),
                                endpoint_category=endpoint_category,
                            )

                            logger.debug(
                                f"📋 Created Placement from federated catalog "
                                f"for {model_id}: "
                                f"VRAM={placement.vram_mb}MB, "
                                f"RAM={placement.ram_mb}MB"
                            )

                            return placement
                    else:
                        logger.debug(
                            f"🔍 Model {model_id} not in gateway "
                            f"{fed_gw.gateway_id}'s model_resources"
                        )

                healthy_count = len(healthy_gateways)
                logger.error(
                    f"❌ build_placement: Model {model_id} not found in any "
                    f"federated gateway's model_resources. "
                    f"Checked {healthy_count} gateways. "
                    f"This likely means telemetry is missing model_resources."
                )
            else:
                logger.error(
                    f"❌ build_placement: No model configuration returned "
                    f"for {model_id} and no federated_manager available."
                )

            return None

        logger.info(
            f"📖 build_placement: Got model_config for {model_id}: "
            f"vram_usage={model_config.vram_usage}, "
            f"ram_usage={model_config.ram_usage}, "
            f"config_type={type(model_config).__name__}"
        )

        ram_mb = model_config.ram_usage
        vram_mb = model_config.vram_usage

        if ram_mb is None or vram_mb is None:
            logger.error(
                f"❌ build_placement: Incomplete resource requirements "
                f"for {model_id}: vram={vram_mb}, ram={ram_mb}. "
                f"ModelMetadata exists but lacks resource data."
            )
            return None

        is_gpu = vram_mb > 0

        # PHASE 2 Investigation: Log before creating Placement
        logger.info(
            f"✅ build_placement: Creating Placement for {model_id}: "
            f"vram={vram_mb}MB, ram={ram_mb}MB, is_gpu={is_gpu}"
        )

        placement = Placement(
            model_id=model_id,
            ram_mb=ram_mb,
            vram_mb=vram_mb,
            is_gpu=is_gpu,
            original_model_id=original_model_id,
            context_length=model_config.context_length,
            endpoint_category=endpoint_category,
        )

        logger.debug(
            f"📋 Created Placement for {model_id}: "
            f"VRAM={placement.vram_mb}MB, RAM={placement.ram_mb}MB, "
            f"context_length={placement.context_length}, "
            f"original={placement.original_model_id}"
        )

        return placement

    except Exception as e:
        logger.error(
            f"❌ build_placement: Exception for {model_id}: {e}",
            exc_info=True,
        )
        return None
