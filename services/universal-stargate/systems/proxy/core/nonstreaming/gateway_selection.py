"""
Gateway selection and slot reservation for request executor.

Handles model loading, gateway selection, and capacity reservation.
"""

import time
from typing import TYPE_CHECKING

from universal_logging import get_logger

from .federated_routing import _route_to_federated_gateway
from .selection_errors import raise_configuration_error

if TYPE_CHECKING:
    from .context import RequestContext

logger = get_logger(__name__)


async def select_gateway_and_load_model(
    context: "RequestContext",
    model_manager,
    gateway_manager,
    event_bus,
    federated_manager=None,
    federated_load_orchestrator=None,
    routing_config: dict | None = None,
    stability_tracker=None,
    compute_type_tracker=None,
    routing_key_tracker=None,
    capacity_pool=None,
    circuit_breaker=None,
) -> tuple[str | None, str | None]:
    """
    Ensure gateway is selected and model is loaded before request execution.

    For federated gateways:
        - Sets context.selected_gateway (Gateway with FederatedGateway ref)
        - Skips local model loading (remote Stargate handles it)
        - Returns (gateway.name, reservation_id)

    Router-Only Master behavior:
        - model_manager = None AND federated_manager != None
        - Decision engine receives only federated gateways
        - ALL requests route to federated (context.is_federated = True)

    Args:
        context: Request context
        model_manager: Model manager for loading models (None in router-only mode)
        gateway_manager: Gateway manager for gateway lookups (None in router-only mode)
        event_bus: Event bus for emitting routing events
        federated_manager: FederatedGatewayManager for router-only mode
        federated_load_orchestrator: Orchestrator for loading models on remote
        compute_type_tracker: MasterRequestTracker for capacity reservation
        routing_key_tracker: MasterRequestTracker for routing key eviction protection
        circuit_breaker: Optional FederationCircuitBreaker for availability checks

    Returns:
        Tuple of (gateway_name, reservation_id) if selected, (None, None) otherwise

    Raises:
        HTTPException: If model loading fails
    """
    routing_start_time = time.time()

    # Router-only mode: model_manager is None, use federated routing
    if model_manager is None:
        if federated_manager is None:
            raise_configuration_error("router-only")

        logger.info(
            f"🔀 Router-only mode: routing to federated gateway for "
            f"{context.selected_model}"
        )
        # Get federation forwarder from orchestrator (wired during startup)
        federation_forwarder = (
            federated_load_orchestrator.forwarder
            if federated_load_orchestrator
            and hasattr(federated_load_orchestrator, "forwarder")
            else None
        )

        return await _route_to_federated_gateway(
            context=context,
            federated_manager=federated_manager,
            federated_load_orchestrator=federated_load_orchestrator,
            federation_forwarder=federation_forwarder,
            event_bus=event_bus,
            routing_start_time=routing_start_time,
            routing_config=routing_config,
            stability_tracker=stability_tracker,
            compute_type_tracker=compute_type_tracker,
            routing_key_tracker=routing_key_tracker,
            capacity_pool=capacity_pool,
            circuit_breaker=circuit_breaker,
        )

    if not context.selected_gateway_instance:
        model_id = context.selected_model
        logger.debug(f"Selecting gateway for model {model_id}")

        # Use model_manager.ensure_model_loaded which handles:
        # 1. Immediate routing (via DecisionEngine)
        # 2. Model loading
        # 3. Queueing if no gateway available (avoids premature 503)
        # Returns Gateway (federated) post-unification
        try:
            result = await model_manager.ensure_model_loaded(
                context.selected_model,  # ModelId object
                sticky=context.model_sticky,
            )
        except Exception as e:
            logger.error(f"Failed to ensure model loaded: {e}")
            raise

        # Post-unification: All results are Gateway (federated)
        logger.info(
            f"📍 ROUTING: model={model_id} sticky={context.model_sticky} "
            f"gateway={result.name} route_type=federated"
        )
        context.selected_gateway = result
        # context.selected_gateway_instance stays None for federated

        # Emit routing event (fire-and-forget)
        if event_bus and context.selected_gateway:
            routing_time_ms = (time.time() - routing_start_time) * 1000
            try:
                from src.scheduling.events import RequestRouted

                # Gateway.ref is FederatedGateway with remote_stargate_url
                gateway_url = getattr(
                    context.selected_gateway.ref, "remote_stargate_url", "unknown"
                )

                await event_bus.publish_async_nowait(
                    RequestRouted(
                        request_id=context.request_id,
                        model_id=str(model_id),  # Serialize for event payload
                        gateway_url=gateway_url,
                        gateway_name=context.selected_gateway.name,
                        timestamp=time.time(),
                        routing_time_ms=routing_time_ms,
                        immediate_route=True,
                    )
                )
            except Exception as e:
                logger.debug(f"Failed to emit REQUEST_ROUTED event: {e}")

    return (
        (context.selected_gateway.name, None)
        if context.selected_gateway
        else (None, None)
    )


def infer_route_type(context: "RequestContext") -> str:
    """
    Infer routing decision type from middleware_actions.

    Returns one of:
        - loaded_on_target: Model already loaded on selected gateway
        - loaded_elsewhere_non_sticky: Model on different gateway (non-sticky)
        - catalog_load: Model loaded from catalog (not previously loaded)
        - queue_fallback: Request waited in queue before routing
    """
    actions = context.middleware_actions

    if any("model_verified_loaded_coordinator" in a for a in actions):
        return "loaded_on_target"
    if any("model_loaded_other_gateway_non_sticky" in a for a in actions):
        return "loaded_elsewhere_non_sticky"
    if any("model_verified_loaded_gateway_status" in a for a in actions):
        return "loaded_on_target"
    if any("model_loading_wait_started" in a for a in actions):
        return "queue_fallback"
    return "catalog_load"
