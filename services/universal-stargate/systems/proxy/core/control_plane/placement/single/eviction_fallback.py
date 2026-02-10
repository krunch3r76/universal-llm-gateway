"""Eviction fallback operations for model routing."""

from typing import TYPE_CHECKING

from universal_logging import get_logger

from .gateway_loading import try_load_on_gateway

if TYPE_CHECKING:
    from gateways import GatewayInstance, SingleGatewayManager

    from ...model_lifecycle.coordination import GlobalModelLoadCoordinator
    from ...model_lifecycle.loading import ModelLoadingOperations
    from ...types import ConfigHelper, ResourceManagerProvider

logger = get_logger(__name__)


async def try_eviction_fallback(
    model_id: str,
    mock_request: dict[str, str],
    gateway_manager: "SingleGatewayManager",
    loading_ops: "ModelLoadingOperations",
    global_load_coordinator: "GlobalModelLoadCoordinator",
    get_resource_manager: "ResourceManagerProvider",
    config: "ConfigHelper",
    *,
    sticky: bool = True,
) -> "GatewayInstance | None":
    """
    Attempt eviction when no gateways have sufficient resources.

    Delegates to the model router which has Priority 3 eviction logic.
    This ensures eviction is attempted before falling back to queue.

    After routing succeeds, actually loads the model on the selected gateway.

    CRITICAL: Re-check coordinator AFTER eviction to prevent race conditions
    where parallel requests both trigger eviction on different gateways.
    """
    try:
        # CRITICAL: Double-check coordinator before expensive eviction
        # Parallel requests might have loaded the model while we were waiting
        if sticky:
            loaded_on = global_load_coordinator.where_is_loaded(model_id)
            if loaded_on:
                logger.debug(
                    f"⚡ Model {model_id} already loaded on {loaded_on} "
                    f"(detected before eviction)"
                )
                # Find and return the gateway instance
                gw = gateway_manager.get_gateway()
                if gw and gw.config.name == loaded_on:
                    return gw

            loading_on = global_load_coordinator.where_is_loading(model_id)
            if loading_on:
                logger.debug(
                    f"⏳ Model {model_id} loading on {loading_on} "
                    f"(detected before eviction), waiting..."
                )
                # Let the queue/retry logic handle waiting
                return None

        # Use the gateway manager's model_router for eviction
        # route_request includes Priority 3 eviction logic
        result = await gateway_manager.route_request(mock_request)

        gateway = result

        if gateway:
            logger.info(
                f"✅ Eviction successful: {model_id} routed to {gateway.config.name}"
            )

            # CRITICAL: Re-check coordinator AFTER eviction completes
            # Another parallel request might have won the race
            if sticky:
                loaded_on = global_load_coordinator.where_is_loaded(model_id)
                if loaded_on:
                    if loaded_on == gateway.config.name:
                        logger.debug(
                            f"✅ Model {model_id} already loaded on "
                            f"{gateway.config.name} "
                            f"(confirmed after eviction)"
                        )
                        return gateway
                    logger.warning(
                        f"⚠️ Race condition: {model_id} loaded on {loaded_on} "
                        f"while we evicted on {gateway.config.name}, routing "
                        f"to {loaded_on}"
                    )
                    # Find and return the actual gateway
                    gw = gateway_manager.get_gateway()
                    if gw and gw.config.name == loaded_on:
                        return gw
                    # Fallback: use our evicted gateway
                    logger.warning(
                        f"⚠️ {loaded_on} not found, using {gateway.config.name}"
                    )

            # Route succeeded - now actually load the model on the gateway
            result = await try_load_on_gateway(
                gateway,
                model_id,
                loading_ops,
                global_load_coordinator,
                get_resource_manager,
                config,
                gateway_manager,
                sticky=sticky,
            )
            if result:
                logger.debug(
                    f"✅ Model {model_id} loaded on {gateway.config.name} "
                    "after eviction"
                )
                return result
            else:
                logger.warning(
                    f"⚠️ Eviction routed to {gateway.config.name} but load "
                    f"failed for {model_id}"
                )
                return None

        logger.debug(f"Eviction fallback did not find gateway for {model_id}")
        return None

    except Exception as e:
        logger.warning(f"Eviction fallback failed for {model_id}: {e}")
        return None
