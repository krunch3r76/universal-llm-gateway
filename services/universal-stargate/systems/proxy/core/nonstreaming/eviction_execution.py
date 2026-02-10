"""
Eviction execution for router-only mode.
"""

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.routing.selection.types import DecisionTrace, Gateway

logger = get_logger(__name__)


async def execute_router_only_eviction(
    federation_forwarder,
    selected_gateway: "Gateway",
    trace: "DecisionTrace",
    request_id: str | None,
    event_bus=None,
) -> bool:
    """
    Execute eviction for router-only mode.

    Args:
        federation_forwarder: FederatedRequestForwarder from FederationIntegration
        selected_gateway: Selected gateway with eviction plan
        trace: Decision trace with candidates
        request_id: Request ID for tracing
        event_bus: EventBus for MODEL_UNLOADED event waiting

    Returns:
        True if eviction succeeded, False otherwise
    """
    from systems.federation.common.types import FederatedGateway
    from systems.routing.eviction.executor import (
        execute_eviction_plan,
        get_eviction_plan_for_gateway,
    )

    eviction_plan = get_eviction_plan_for_gateway(trace, selected_gateway.name)

    # No eviction needed
    if eviction_plan is None or not eviction_plan.models_to_evict:
        return True

    # Validate gateway ref type
    if not isinstance(selected_gateway.ref, FederatedGateway):
        logger.warning(
            f"Cannot execute eviction: gateway ref is not FederatedGateway "
            f"(got {type(selected_gateway.ref).__name__})"
        )
        return False

    # Validate forwarder
    if not federation_forwarder:
        logger.error(
            "❌ Cannot execute eviction: no federation_forwarder configured. "
            "Eviction requires FederatedRequestForwarder (Master mode)."
        )
        return False

    return await execute_eviction_plan(
        forwarder=federation_forwarder,
        federated_gateway=selected_gateway.ref,
        eviction_plan=eviction_plan,
        gateway_name=selected_gateway.name,
        request_id=request_id,
        event_bus=event_bus,
    )
