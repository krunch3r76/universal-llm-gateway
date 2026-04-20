from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Coroutine
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from systems.federation.common.config.schema import StargateMode
from systems.pipeline.core.execution.dispatch_journal import (
    initialize_schema,
    journal_terminal,
    prune_expired,
)
from systems.pipeline.execution_summary import get_summary_writer

from .component_factory import (
    configure_token_and_parameter_managers,
    initialize_aggregate_model_availability,
    initialize_hot_reload,
    initialize_intelligence_profiles,
    initialize_pipeline_system,
    initialize_request_components,
)
from .event_system import (
    emit_system_started_event,
    initialize_event_consumers,
    initialize_gateway_logger,
    initialize_shutdown_handler,
    register_gateways,
)
from .gateway_bootstrap import (
    initialize_gateway_manager,
    initialize_http_client,
    initialize_resource_manager,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from ..proxy import StargateProxy

logger = get_logger(__name__)


def _clear_snapshot_files_preserve_directories(snapshot_dir: Path) -> None:
    """
    Clear snapshot files while preserving standard stage directories.

    Preserves only standard stage directories (before/, after/, response-from-gateway/,
    response-to-client/). Deletes old task directories and all files.

    Rationale: If a human shells into a standard subdirectory, deleting it on restart
    orphans their cwd and forces re-navigation. Preserving standard directories avoids
    this UX footgun while clearing old content.
    """
    import shutil

    # Standard stage directories created by write_request_snapshot()
    standard_stages = {"before", "after", "response-from-gateway", "response-to-client"}

    for item in snapshot_dir.iterdir():
        if not item.is_dir():
            # Delete files and symlinks at top level.
            item.unlink()
            continue
        if item.name not in standard_stages:
            # Delete non-standard directories (old task dirs).
            shutil.rmtree(item)
            continue
        # Preserve standard stage directory, delete only its contents.
        for subitem in item.iterdir():
            if subitem.is_dir():
                # Delete unexpected nested directories in stage dirs.
                shutil.rmtree(subitem)
            else:
                subitem.unlink()


def _require_config_dict(proxy: StargateProxy) -> dict[str, Any]:
    """
    Get config dict from proxy or fail-fast.

    Raises:
        AttributeError: If proxy.config.config is not accessible
    """
    if not hasattr(proxy.config, "config"):
        raise AttributeError(
            "proxy.config.config is not accessible. "
            "StargateProxy must be initialized with a config that exposes .config dict."
        )
    return proxy.config.config


def _run_startup_cleanup(name: str, cleanup_func: Callable[[], None]) -> None:
    """Run startup cleanup with consistent logging and failure visibility."""
    try:
        cleanup_func()
        logger.info("Cleared %s on startup", name)
    except Exception as e:
        logger.warning(
            "Failed to cleanup %s on startup: %s",
            name,
            e,
            exc_info=True,
        )


def _schedule_supervised_task(coro: Coroutine[Any, Any, object], name: str) -> None:
    """
    Schedule a background task and surface exceptions in logs.

    Used when wiring callbacks asynchronously so startup and event handlers remain
    non-blocking while task failures remain diagnosable.
    """
    task = asyncio.create_task(coro, name=name)

    def _on_done(done_task: asyncio.Task[Any]) -> None:
        """Handle completion callback for a supervised background task.

        Args:
            done_task: Completed asyncio task instance.
        """
        if done_task.cancelled():
            logger.debug("Background task cancelled: %s", name)
            return
        exc = done_task.exception()
        if exc is not None:
            logger.error(
                "Background task failed: %s: %s",
                name,
                exc,
                exc_info=True,
            )

    task.add_done_callback(_on_done)


def _start_dispatch_journal_prune_loop(
    proxy: StargateProxy,
    *,
    retention_seconds: float,
) -> None:
    """Run hourly sqlite-journal retention pruning in the background."""

    async def _dispatch_journal_prune_loop() -> None:
        while True:
            try:
                await asyncio.sleep(3600.0)
                await prune_expired(
                    retention_seconds,
                    event_bus=proxy.event_bus,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning("Dispatch journal prune failed: %s", exc)

    _schedule_supervised_task(
        _dispatch_journal_prune_loop(),
        name="dispatch-journal-prune-loop",
    )


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
            logger.info(f"Debug event server started: {socket_path}")

    # Cleanup old pipeline summaries on startup
    _run_startup_cleanup(
        "pipeline summaries",
        lambda: get_summary_writer().cleanup_all_pipelines(),
    )

    # Cleanup request snapshots on startup
    data_dir = os.getenv("DATA_DIR", "/tmp")
    snapshot_dir = Path(data_dir) / "stargate-request-snapshots"
    if snapshot_dir.exists():
        _run_startup_cleanup(
            "request snapshots",
            lambda: _clear_snapshot_files_preserve_directories(snapshot_dir),
        )

    # Cleanup pipeline failures on startup
    log_dir = os.getenv("LOG_DIR", "/tmp/logs/universal-stargate")
    failures_dir = Path(log_dir) / "pipeline_failures"
    if failures_dir.exists():
        import shutil

        def _cleanup_failures_dir() -> None:
            shutil.rmtree(failures_dir)
            failures_dir.mkdir(parents=True, exist_ok=True)

        _run_startup_cleanup("pipeline failures", _cleanup_failures_dir)

    # Skip gateway-specific initialization in router-only mode
    if gateway_name is not None:
        await initialize_http_client(proxy)
        await initialize_gateway_manager(proxy)
        await initialize_resource_manager(proxy)
        _wire_request_inference_started_event(proxy)

        await configure_token_and_parameter_managers(proxy)
    else:
        logger.info("Router-only mode: Skipping gateway initialization")

    # Initialize capacity pool BEFORE federation
    from systems.routing.capacity.pool import CapacityPool

    capacity_pool_config = proxy.config.get_capacity_pool_config()
    capacity_pool = CapacityPool(
        event_bus=proxy.event_bus,
        max_queue_depth=capacity_pool_config["max_queue_depth"],
    )
    logger.info(
        "✅ CapacityPool initialized (max_queue_depth=%d)",
        capacity_pool_config["max_queue_depth"],
    )

    proxy.capacity_pool = capacity_pool

    # Initialize federation BEFORE request components
    # CRITICAL: RequestExecutor needs federation_forwarder for token counting
    # CRITICAL: Remote mode needs model_manager for token counting orchestration
    # CRITICAL: Remote mode needs gateway_manager for telemetry endpoint
    # CRITICAL: Edge mode needs gateway_manager for federation server
    if app is not None:
        from systems.federation import init_federation

        # Get gateway socket path for token counting (Master/Standalone modes)
        # None in router-only mode

        # Pass model_manager for Remote/Edge mode
        # Pass gateway_manager for Remote/Edge mode telemetry/federation
        # Pass event_bus for HTTP telemetry poller (Master mode)
        # In router-only mode, pass None for model_manager and gateway_manager

        # DIAGNOSTIC: Log what we're passing
        logger.info(
            f"🔍 Federation init params: gateway_name={gateway_name}, "
            f"gateway_manager={proxy.gateway_manager}, "
            f"model_manager={proxy.resource_aware_model_manager}"
        )

        proxy.federation_integration = await init_federation(
            app,
            gateway_socket_path=gateway_socket_path,
            model_manager=proxy.resource_aware_model_manager if gateway_name else None,
            gateway_manager=proxy.gateway_manager if gateway_name else None,
            event_bus=proxy.event_bus,
            stargate_config=proxy.config,
            health_observer=proxy.model_health_store.observe,
        )
        logger.info("✅ Federation integration initialized")

        # Wire CapacityPool to FederatedGatewayManager for telemetry-driven
        # capacity updates. Required in ALL Master modes (including router-only).
        if (
            proxy.federation_integration
            and proxy.federation_integration.federated_manager
            and hasattr(proxy, "capacity_pool")
            and proxy.capacity_pool
        ):
            proxy.federation_integration.federated_manager.set_capacity_pool(
                proxy.capacity_pool
            )
            logger.info("✅ CapacityPool wired to federated gateway manager")

        # Determine if this is Relay mode (Remote with local_edge, no local Gateway)
        is_relay_mode = (
            proxy.federation_integration
            and proxy.federation_integration.config.mode == StargateMode.REMOTE
            and proxy.federation_integration.config.local_edge is not None
            and not proxy.federation_integration.config.disable_websocket
        )

        # Wire federation manager to gateway manager for routing
        # (skip in router-only mode and relay mode)
        if gateway_name is not None and not is_relay_mode:
            _wire_federation_manager(proxy)

            # Wire gateway telemetry to federation (Remote mode with local Gateway)
            # CRITICAL: This enables telemetry flow from local Gateway → Master
            _wire_federation_telemetry(proxy)
        elif is_relay_mode:
            logger.info(
                "Relay mode: Starting periodic telemetry for local_edge forwarding"
            )
            _start_relay_periodic_telemetry(proxy)
        else:
            logger.info("Router-only mode: Skipping gateway telemetry wiring")
    else:
        proxy.federation_integration = None
        logger.debug("No FastAPI app provided - federation initialization skipped")

    # Wire federated load orchestrator (Master mode only)
    # Orchestrator is created by MasterModeSetup with config from FederationConfig
    # This function wires it to proxy and model manager
    _wire_federated_load_orchestrator(proxy)

    # Wire forwarder to model router for eviction execution (Master mode only)
    _wire_forwarder_to_model_router(proxy)

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

    tracker = getattr(proxy, "pipeline_dispatch_tracker", None)
    if tracker is not None:
        await initialize_schema()
        tracker.set_journal_writer(
            partial(
                journal_terminal,
                event_bus=proxy.event_bus,
            )
        )
        _start_dispatch_journal_prune_loop(
            proxy,
            retention_seconds=tracker.retention_seconds,
        )
        logger.info("✅ Dispatch journal initialized for terminal record persistence")

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


def _wire_federation_manager(proxy: StargateProxy) -> None:
    """
    Wire federation manager to gateway manager for routing.

    CRITICAL: Skips wiring in REMOTE mode - Remote Stargates only route to
    their local Gateway, not federatively. Prevents infinite recursion when
    Remote tries to load models.

    Requires proxy.config.config to be accessible (fail-fast if not).
    """
    if not (
        proxy.federation_integration
        and proxy.federation_integration.federated_manager
        and proxy.gateway_manager
    ):
        return

    # CRITICAL: Skip federation wiring in Remote mode
    # Remote Stargates should only route to their local Gateway, not federatively
    # Prevents infinite recursion: Remote.ensure_model_loaded →
    # ModelRouter.route_request → collect_stargates →
    # federated_manager.get_healthy_gateways → infinite loop
    from systems.federation.common.config import StargateMode

    fed_config = proxy.federation_integration.config
    if fed_config.mode == StargateMode.REMOTE:
        logger.info(
            "Remote mode: Skipping federation manager wiring (local-only routing)"
        )
        return

    # Require config (fail-fast, no silent fallback)
    config = _require_config_dict(proxy)

    proxy.gateway_manager.set_federated_manager(
        proxy.federation_integration.federated_manager,
        config=config,
    )
    logger.info("✅ Federation manager wired to gateway manager for routing")


def _wire_federation_telemetry(proxy: StargateProxy) -> None:
    """
    Wire local Gateway telemetry to federation (Remote/Edge modes).

    This enables telemetry flow:
    - Remote: Gateway → RemoteTelemetrySender → Master
    - Edge: Gateway → EdgeFederationServer → Master/Relay

    Pre: proxy.federation_integration and proxy.gateway_manager initialized
    Post: Callback registered to wire telemetry when Gateway connects

    CRITICAL: Wiring happens on Gateway connection, not during startup.
    Gateway must be connected before telemetry callbacks can be wired.
    """
    if not proxy.federation_integration:
        return

    if proxy.gateway_manager is None:
        logger.warning(
            f"Cannot wire federation telemetry: gateway_manager not available "
            f"(proxy.gateway_manager={proxy.gateway_manager}, "
            f"proxy._is_execution_capable="
            f"{getattr(proxy, '_is_execution_capable', 'N/A')})"
        )
        return

    if proxy.event_bus is None:
        logger.warning("Cannot wire federation telemetry: event_bus not available")
        return

    # Define the telemetry wiring function
    async def wire_on_gateway_connect() -> None:
        """Wire telemetry callbacks after Gateway connects."""
        logger.debug("🔧 wire_on_gateway_connect() called")
        gateway = proxy.gateway_manager.get_gateway()
        if not gateway:
            logger.debug("Gateway not yet connected, skipping telemetry wiring")
            return

        logger.debug(f"✅ Gateway found: {gateway.config.base_url}")

        # Get the WebSocket client from the gateway
        ws_client = gateway.client.ws_client

        # Use gateway's base URL for telemetry reporting
        gateway_url = gateway.config.base_url
        logger.debug(f"Using gateway base_url for telemetry: {gateway_url}")

        # Wire the callbacks (Remote mode)
        logger.debug("Calling wire_gateway_telemetry()")
        proxy.federation_integration.wire_gateway_telemetry(ws_client, gateway_url)

        # Wire Edge mode callbacks if applicable
        logger.debug("Calling wire_edge_gateway_telemetry()")
        proxy.federation_integration.wire_edge_gateway_telemetry(ws_client, gateway_url)

        logger.info(
            "✅ Federation telemetry callbacks wired for Gateway: %s", gateway_url
        )

    # Register event handler for GATEWAY_STATE_CHANGED events
    from universal_event_bus import Event

    from src.scheduling.events import GATEWAY_STATE_CHANGED

    async def on_gateway_state_changed(event: Event) -> None:
        """
        Handle GATEWAY_STATE_CHANGED event to wire telemetry on connect.

        Expected payload (from GatewayStateChanged factory):
            - url: str
            - connectivity: str ("reachable" | "unreachable")
            - health: str ("healthy" | "unhealthy" | "unknown")
            - previous_connectivity: str | None
            - previous_health: str | None
            - transition_type: str
            - check_duration_ms: int
        """
        logger.debug(f"🔔 on_gateway_state_changed called: event={event}")
        payload = event.payload
        if not isinstance(payload, dict):
            logger.debug(f"⚠️  Payload is not dict: {type(payload)}")
            return

        # Only wire on connection events (not disconnection)
        connectivity = payload["connectivity"]
        logger.debug(f"🔔 Gateway connectivity: {connectivity}")
        if connectivity == "reachable":
            logger.info("🔌 Gateway connected - wiring federation telemetry now")
            await wire_on_gateway_connect()
        else:
            logger.debug(
                f"Gateway not reachable (connectivity={connectivity}) - "
                "skipping telemetry wiring"
            )

    # Subscribe to gateway state changes
    proxy.event_bus.subscribe_async(GATEWAY_STATE_CHANGED, on_gateway_state_changed)
    logger.info("Registered telemetry wiring callback for Gateway connection events")

    # Also wire immediately if Gateway already connected (rare, but handle it)
    gateway = proxy.gateway_manager.get_gateway()
    if gateway:
        _schedule_supervised_task(
            wire_on_gateway_connect(),
            name="wire-federation-telemetry-immediate",
        )
        logger.info("Gateway already connected - wiring telemetry immediately")


def _start_relay_periodic_telemetry(proxy: StargateProxy) -> None:
    """
    Start periodic telemetry task for Relay mode (Remote with local_edge).

    Relay has no local Gateway but needs periodic telemetry to keep Master's
    connection fresh and prevent staleness warnings.

    Uses RemoteIntegration's method to start the task.
    Expected mode integration type is RemoteIntegration in relay deployments.
    """
    import asyncio

    if not proxy.federation_integration:
        return

    if not proxy.federation_integration.can_start_relay_periodic_telemetry():
        logger.warning("Cannot start relay periodic telemetry: method not available")
        return

    # Start the periodic task
    asyncio.create_task(
        proxy.federation_integration.start_relay_periodic_telemetry(),
        name="relay-periodic-telemetry-starter",
    )
    logger.info("✅ Started relay periodic telemetry task")


def _wire_federated_load_orchestrator(proxy: StargateProxy) -> None:
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
            f"({len(fed_config.remotes)} configured)"
        )


def _wire_forwarder_to_model_router(proxy: StargateProxy) -> None:
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


def _wire_request_inference_started_event(proxy: StargateProxy) -> None:
    """Bridge Gateway runtime-start telemetry into Stargate request events."""
    if proxy.event_bus is None:
        logger.warning(
            "Cannot wire request inference start callback: event_bus not available"
        )
        return
    if proxy.gateway_manager is None:
        logger.warning(
            "Cannot wire request inference start callback: "
            "gateway_manager not available"
        )
        return

    gateway = proxy.gateway_manager.gateway
    if gateway is None:
        logger.warning(
            "Cannot wire request inference start callback: gateway not initialized"
        )
        return

    ws_client = gateway.client.ws_client

    from src.scheduling.events import RequestInferenceStarted

    async def _on_request_inference_started(
        request_id: str,
        model_id: str,
        gateway_url: str,
        correlation_id: str | None,
    ) -> None:
        try:
            await proxy.event_bus.publish_nowait(
                RequestInferenceStarted(
                    request_id=request_id,
                    model_id=model_id,
                    gateway_url=gateway_url,
                    correlation_id=correlation_id,
                )
            )
        except Exception:
            logger.exception(
                "Failed to publish request.inference.started from gateway telemetry: "
                "request_id=%s model_id=%s gateway_url=%s",
                request_id,
                model_id,
                gateway_url,
            )

    ws_client.on_request_inference_started(_on_request_inference_started)
    logger.info("Registered request.inference.started callback from Gateway telemetry")
