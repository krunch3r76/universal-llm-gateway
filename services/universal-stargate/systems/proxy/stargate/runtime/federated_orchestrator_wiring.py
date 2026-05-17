from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

from systems.federation.common.config.schema import StargateMode

if TYPE_CHECKING:
    from ..proxy import StargateProxy

logger = get_logger(__name__)


def wire_federated_load_orchestrator(proxy: StargateProxy) -> None:
    """
    Wire federated load orchestrator from FederationIntegration to proxy.

    Orchestrator is created by MasterModeSetup with config from
    FederationConfig.orchestration. This function wires it to proxy and model manager.

    Pre: proxy.federation_integration initialized (via init_federation)
    Post: proxy.federated_load_orchestrator = FederationIntegration.load_orchestrator
          proxy.resource_aware_model_manager wired with orchestrator (if exists)

    INVARIANT: Master mode ⟹ orchestrator wired.
    If a local Gateway is present, model manager wiring is also mandatory.
    FAIL-FAST: Raises RuntimeError if Master mode but load_orchestrator is None
    """
    if not proxy.federation_integration:
        logger.debug("Federation not enabled, skipping orchestrator wiring")
        return

    # Check if Master mode
    if proxy.federation_integration.config.mode != StargateMode.MASTER:
        logger.debug("Not Master mode, skipping orchestrator wiring")
        return

    # Get orchestrator from FederationIntegration (created by MasterModeSetup)
    orchestrator = proxy.federation_integration.load_orchestrator
    if orchestrator is None:
        raise RuntimeError(
            "STARTUP FAILURE: Master mode but "
            "FederationIntegration.load_orchestrator is None. "
            "MasterModeSetup failed to create orchestrator. "
            "Check federation configuration."
        )

    # Store on proxy for access
    proxy.federated_load_orchestrator = orchestrator

    # Wire into model manager (only if local gateway exists)
    # Router-only masters don't have local model manager, which is fine
    if proxy.gateway_manager and proxy.resource_aware_model_manager is None:
        raise RuntimeError(
            "STARTUP FAILURE: Master mode with local gateway requires "
            "resource_aware_model_manager for orchestrator wiring. "
            "This indicates an internal setup error."
        )
    if proxy.resource_aware_model_manager:
        proxy.resource_aware_model_manager.set_federated_load_orchestrator(orchestrator)
        logger.info("✅ Federated load orchestrator wired to model manager")
    else:
        fed_config = proxy.federation_integration.config
        logger.info(
            "ℹ️  Router-only Master: Orchestrator routes to Remote Stargates "
            "(%d configured)",
            len(fed_config.remotes),
        )


def wire_forwarder_to_model_router(proxy: StargateProxy) -> None:
    """
    Wire federation forwarder to ModelRouter for eviction execution.

    Pre: proxy.federation_integration initialized (Master mode)
    Post: proxy.gateway_manager.model_router._forwarder = forwarder

    INVARIANT: Master mode with local gateway ⟹ forwarder wired to model_router.
    """
    if not proxy.federation_integration:
        return

    # Only wire in Master mode
    if proxy.federation_integration.config.mode != StargateMode.MASTER:
        return

    # Router-only mode: gateway_manager may not exist
    if not proxy.gateway_manager:
        logger.debug("Cannot wire forwarder: no gateway_manager (router-only mode)")
        return

    forwarder = proxy.federation_integration.forwarder
    if forwarder is None:
        logger.error("❌ Master mode but forwarder is None - eviction disabled")
        return

    # Require model router from gateway manager's public startup contract.
    if proxy.gateway_manager.model_router is None:
        raise RuntimeError(
            "STARTUP FAILURE: gateway_manager.model_router is unavailable for "
            "forwarder wiring. initialize_gateway_manager must initialize model_router."
        )
    proxy.gateway_manager.model_router.set_forwarder(forwarder)
