from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from systems.federation.common.config.schema import StargateMode

from .startup_task_supervision import schedule_supervised_task

if TYPE_CHECKING:
    from fastapi import FastAPI

    from ..proxy import StargateProxy

logger = get_logger(__name__)


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
            "Cannot wire federation telemetry: gateway_manager not available "
            "(proxy.gateway_manager=%s, proxy._is_execution_capable=%s)",
            proxy.gateway_manager,
            getattr(proxy, "_is_execution_capable", "N/A"),
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

        logger.debug("✅ Gateway found: %s", gateway.config.base_url)

        # Get the WebSocket client from the gateway
        ws_client = gateway.client.ws_client

        # Use gateway's base URL for telemetry reporting
        gateway_url = gateway.config.base_url
        logger.debug("Using gateway base_url for telemetry: %s", gateway_url)

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
        logger.debug("🔔 on_gateway_state_changed called: event=%s", event)
        payload = event.payload
        if not isinstance(payload, dict):
            logger.debug("⚠️  Payload is not dict: %s", type(payload))
            return

        # Only wire on connection events (not disconnection)
        connectivity = payload["connectivity"]
        logger.debug("🔔 Gateway connectivity: %s", connectivity)
        if connectivity == "reachable":
            logger.info("🔌 Gateway connected - wiring federation telemetry now")
            await wire_on_gateway_connect()
        else:
            logger.debug(
                "Gateway not reachable (connectivity=%s) - skipping telemetry wiring",
                connectivity,
            )

    # Subscribe to gateway state changes
    proxy.event_bus.subscribe_async(GATEWAY_STATE_CHANGED, on_gateway_state_changed)
    logger.info("Registered telemetry wiring callback for Gateway connection events")

    # Also wire immediately if Gateway already connected (rare, but handle it)
    gateway = proxy.gateway_manager.get_gateway()
    if gateway:
        schedule_supervised_task(
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
    if not proxy.federation_integration:
        return

    if not proxy.federation_integration.can_start_relay_periodic_telemetry():
        logger.warning("Cannot start relay periodic telemetry: method not available")
        return

    # Start the periodic task (supervised so exceptions surface in logs)
    schedule_supervised_task(
        proxy.federation_integration.start_relay_periodic_telemetry(),
        name="relay-periodic-telemetry-starter",
    )
    logger.info("✅ Started relay periodic telemetry task")


async def initialize_federation_runtime(
    proxy: StargateProxy,
    app: FastAPI | None,
    gateway_name: str | None,
    gateway_socket_path: str | None,
) -> None:
    """
    Initialize federation integration and wire telemetry/routing for startup.

    Extracts federation block from startup_proxy. Sets proxy.federation_integration.
    Wires capacity pool, chooses telemetry/relay/router-only paths.

    Runs before request component init.
    """
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
            "🔍 Federation init params: gateway_name=%s, "
            "gateway_manager=%s, "
            "model_manager=%s",
            gateway_name,
            proxy.gateway_manager,
            proxy.resource_aware_model_manager,
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
