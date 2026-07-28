from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

from .capacity_pool_startup import initialize_capacity_pool
from .component_factory import (
    configure_token_and_parameter_managers,
    initialize_aggregate_model_availability,
    initialize_hot_reload,
    initialize_intelligence_profiles,
    initialize_pipeline_system,
    initialize_request_components,
)
from .dispatch_journal_startup import initialize_dispatch_journal
from .event_system import (
    emit_system_started_event,
    initialize_event_consumers,
    initialize_gateway_logger,
    initialize_shutdown_handler,
    register_gateways,
)
from .federated_orchestrator_wiring import (
    wire_federated_load_orchestrator,
    wire_forwarder_to_model_router,
)
from .federation_startup import initialize_federation_runtime
from .gateway_bootstrap import (
    initialize_gateway_manager,
    initialize_http_client,
    initialize_resource_manager,
)
from .gateway_telemetry_bridge import wire_request_inference_started_event
from .startup_cleanup import cleanup_startup_artifacts

if TYPE_CHECKING:
    from fastapi import FastAPI

    from ..proxy import StargateProxy

logger = get_logger(__name__)


async def startup_proxy(proxy: StargateProxy, app: FastAPI | None = None) -> None:
    """
    Initialize all runtime components for the Stargate proxy.

    Startup order is deliberate because multiple subsystems depend on each other:
    cleanup -> gateway bootstrap -> federation -> request components -> intelligence
    profiles -> pipelines -> event consumers. Federation wiring precedes request
    component initialization so routing and token accounting dependencies are ready.

    Args:
        proxy: Initialized proxy instance to wire and bootstrap.
        app: Optional FastAPI app, required for federation HTTP integration.
    """
    if app is not None:
        proxy._fastapi_app = app

    gateway_config = proxy.gateway_config
    gateway_name = gateway_config.name if gateway_config else None
    gateway_socket_path = gateway_config.socket_path if gateway_config else None

    # Router-only mode: no local gateway
    if gateway_name is None:
        logger.info("Starting Stargate Proxy in router-only mode (no local gateway)")
    else:
        logger.info("Starting Stargate Proxy with single gateway: %s", gateway_name)

    # Start debug event broadcaster if configured (early - want to capture all events)
    debug_broadcaster = proxy.debug_broadcaster
    if debug_broadcaster:
        await debug_broadcaster.start_debug_server()
        debug_config = proxy.config.get_debug_event_config()
        socket_path = debug_config.get("socket_path")
        if socket_path:
            logger.info("Debug event server started: %s", socket_path)

    # Run startup cleanup (summaries, snapshots w/ stage dirs preserved, failures).
    cleanup_startup_artifacts()

    # Skip gateway-specific initialization in router-only mode
    if gateway_name is not None:
        await initialize_http_client(proxy)
        await initialize_gateway_manager(proxy)
        await initialize_resource_manager(proxy)
        wire_request_inference_started_event(proxy)

        await configure_token_and_parameter_managers(proxy)
    else:
        logger.info("Router-only mode: Skipping gateway initialization")

    # Initialize capacity pool BEFORE federation
    initialize_capacity_pool(proxy)

    # Initialize federation BEFORE request components
    # CRITICAL: RequestExecutor needs federation_forwarder for token counting
    # CRITICAL: Remote mode needs model_manager for token counting orchestration
    # CRITICAL: Remote mode needs gateway_manager for telemetry endpoint
    # CRITICAL: Edge mode needs gateway_manager for federation server
    await initialize_federation_runtime(proxy, app, gateway_name, gateway_socket_path)

    # Wire federated load orchestrator (Master mode only)
    # Orchestrator is created by MasterModeSetup with config from FederationConfig
    # This function wires it to proxy and model manager
    wire_federated_load_orchestrator(proxy)

    # Wire forwarder to model router for eviction execution (Master mode only)
    wire_forwarder_to_model_router(proxy)

    # Initialize request components (depends on federation_forwarder)
    # Master mode (no local gateway) uses different initialization path
    if gateway_name is not None:
        initialize_request_components(proxy)
    else:
        from .component_factory import initialize_master_request_components

        initialize_master_request_components(proxy)
        logger.info("✅ Master request components initialized")

    # Initialize intelligence profile store (depends on federation/cloud proxy)
    await initialize_intelligence_profiles(proxy)

    await initialize_aggregate_model_availability(proxy)

    # Initialize pipeline system (depends on request_executor)
    await initialize_pipeline_system(proxy)

    await initialize_dispatch_journal(proxy)

    from .pipeline_orphan_sweep import cancel_running_pipelines_for_shutdown

    try:
        reaped = await cancel_running_pipelines_for_shutdown(
            proxy, reason="startup_reconcile"
        )
        if reaped:
            logger.warning(
                "Startup reconciled %d in-process running pipeline(s) "
                "(unclean prior shutdown)",
                reaped,
            )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Startup pipeline orphan reconcile failed: %s", exc)

    await initialize_hot_reload(proxy)

    # Skip gateway-specific event systems in router-only mode
    if gateway_name is not None:
        initialize_gateway_logger(proxy)
        await initialize_event_consumers(proxy)

        register_gateways(proxy)
        initialize_shutdown_handler(proxy)
    else:
        logger.info("Router-only mode: Skipping gateway event systems")

    # Start background cleanup for orphaned requests (defense in depth)
    from src.core.gateway_tracker import gateway_tracker

    gateway_tracker.start_background_cleanup(
        interval_seconds=60,  # Check every minute
        max_age_seconds=600,  # Clean up requests >10min old
    )

    await emit_system_started_event(proxy)

    logger.info("✅ Stargate Proxy started successfully")
