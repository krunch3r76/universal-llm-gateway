"""Eviction execution for Master mode (remote gateway eviction)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from model_id import ModelId
    from universal_event_bus import EventBus

    from systems.federation.master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )
    from systems.federation.master.routing.forward import FederatedRequestForwarder
    from systems.routing.selection.types import DecisionTrace, Gateway

logger = get_logger(__name__)


async def execute_master_eviction(
    federation_forwarder: FederatedRequestForwarder | None,
    federated_manager: FederatedGatewayManager | None,
    selected_gateway: Gateway,
    trace: DecisionTrace,
    request_id: str | None,
    event_bus: EventBus | None = None,
) -> bool:
    """
    Execute eviction on a remote gateway (Master mode).

    Args:
        federation_forwarder: FederatedRequestForwarder from FederationIntegration
        federated_manager: FederatedGatewayManager for optimistic state updates
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

    # CRITICAL: Mark evicted models as "transitioning" immediately.
    #
    # This prevents concurrent routing from treating a soon-to-be-unloaded model as
    # "loaded" (T1) during the eviction window.
    marked_transitioning: list[ModelId] = []
    if federated_manager is not None:
        for model_id in eviction_plan.models_to_evict:
            if federated_manager.mark_loading_optimistic(
                selected_gateway.ref.gateway_id, model_id
            ):
                marked_transitioning.append(model_id)

    ok = False
    try:
        ok = await execute_eviction_plan(
            forwarder=federation_forwarder,
            federated_gateway=selected_gateway.ref,
            eviction_plan=eviction_plan,
            gateway_name=selected_gateway.name,
            request_id=request_id,
            event_bus=event_bus,
        )
        return ok
    finally:
        # If eviction failed, clear transitioning mark so we don't poison routing.
        #
        # On success, telemetry MODEL_UNLOADED will clear the state (and the model
        # will no longer be in loaded_models anyway).
        if federated_manager is not None and marked_transitioning and not ok:
            # Best-effort cleanup; never mask the eviction result.
            for model_id in marked_transitioning:
                try:
                    await federated_manager.clear_model_loading(
                        selected_gateway.ref.gateway_id, model_id
                    )
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        "❌ Failed to clear transitioning mark for %s on %s: %s",
                        model_id,
                        selected_gateway.ref.gateway_id,
                        e,
                    )
