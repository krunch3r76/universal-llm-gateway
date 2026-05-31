from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from universal_logging import get_logger

from gateways import SingleGatewayManager

from ...core.resource_aware_model_manager import ResourceAwareModelManager

if TYPE_CHECKING:
    from ...stargate.proxy import StargateProxy

logger = get_logger(__name__)


def _build_loaded_model_sync_callback(coordinator):
    """
    Build the gateway->coordinator sync callback.

    Called on gateway connect/reconnect after INIT is processed.

    Responsibilities:
    - Sync loaded model placement for routing correctness
    - Wake any coordinator waiters that were blocked on a load completing while
      the gateway WebSocket was disconnected

    Invariant: ∀ model_id ∈ INIT.loaded_models ⟹ coordinator_state(model_id)=loaded
    """

    def _sync_loaded_models(gateway_name: str, loaded_models: frozenset[str]) -> None:
        # Sync loaded models to coordinator (handles stale entry removal)
        coordinator.sync_loaded_models_for_gateway(gateway_name, loaded_models)

        # Wake pending loaders: if a load completed while WS was down, the
        # coordinator may still have an in-flight loading event; resolve it via
        # the standard event path so waiters wake in O(1).
        woken_count = 0
        for model_id in loaded_models:
            if coordinator.where_is_loading(model_id) == gateway_name:
                coordinator.on_model_loaded_event(model_id, gateway_name)
                woken_count += 1

        if woken_count > 0:
            logger.info(
                f"🔔 Replayed MODEL_LOADED for {woken_count} model(s) "
                f"on {gateway_name} (waiters woken via INIT sync)"
            )

    return _sync_loaded_models


async def initialize_http_client(proxy: StargateProxy) -> None:
    """Create the shared httpx.AsyncClient with sane defaults.

    If the primary gateway uses Unix sockets, configures the client with
    Unix Domain Socket transport. This is critical for generic forwarding
    endpoints (/v1/audio/transcriptions, etc.) that don't receive a
    gateway-specific ForwardContext.

    Invariant: proxy.http_client transport matches primary gateway transport
    """
    limits = httpx.Limits(
        max_keepalive_connections=5,
        max_connections=10,
        keepalive_expiry=30.0,
    )

    # Check if primary gateway uses Unix socket transport
    primary_config = proxy._gateway_config  # noqa: SLF001 - bootstrap data

    if primary_config.socket_path:
        # Unix socket transport - used for network-isolated deployments
        transport = httpx.AsyncHTTPTransport(uds=primary_config.socket_path)
        proxy.http_client = httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost",  # Required but ignored for UDS
            timeout=400.0,
            limits=limits,
            http2=False,
        )
        logger.info(
            "✅ HTTP client initialized with Unix socket transport: %s",
            primary_config.socket_path,
        )
    else:
        # TCP transport (standard)
        proxy.http_client = httpx.AsyncClient(
            timeout=400.0,
            limits=limits,
            http2=False,
        )
        logger.debug("HTTP client initialized with TCP transport")


async def initialize_gateway_manager(proxy: StargateProxy) -> None:
    """Initialize SingleGatewayManager WITHOUT connecting to gateway yet.

    CRITICAL: This only creates the gateway manager instance. The actual
    connection happens in initialize_resource_manager() after the coordinator's
    executor is started. This prevents "Executor not running" errors when the
    gateway connects and sends its INIT message with loaded models.
    """
    # Single gateway configuration (1:1 relationship)
    gateway_config = proxy._gateway_config  # noqa: SLF001 - internal bootstrap

    proxy.gateway_manager = SingleGatewayManager(
        gateway_config=gateway_config,
        event_bus=proxy.event_bus,
    )

    logger.info(
        "✅ Gateway manager created (connection deferred until coordinator ready): %s",
        gateway_config.name,
    )


async def initialize_resource_manager(proxy: StargateProxy) -> None:
    """Initialize the resource-aware model manager and connect to gateway.

    CRITICAL: This starts the coordinator's executor BEFORE connecting to the
    gateway. This ensures that model sync callbacks can be queued when the
    gateway sends its INIT message with loaded models.
    """
    if proxy.gateway_manager is None:
        logger.error("❌ Gateway manager not initialized")
        return

    try:
        proxy.resource_aware_model_manager = ResourceAwareModelManager(
            gateway_manager=proxy.gateway_manager,
            config=proxy.config,
            event_bus=proxy.event_bus,
        )
        await proxy.resource_aware_model_manager.initialize()
        gateway_config = proxy._gateway_config  # noqa: SLF001
        logger.info(
            "✅ Resource-aware model manager online for gateway: %s",
            gateway_config.name,
        )

        # Ensure ModelRouter is initialized with proper config
        # Pass the entire StargateConfig.config dict so ModelRouter can extract routing
        # config
        routing_config = proxy.config.config if hasattr(proxy.config, "config") else {}
        proxy.gateway_manager.set_config(routing_config)
        proxy.gateway_manager._ensure_model_router()  # noqa: SLF001

        proxy.gateway_manager.model_router.set_load_waiter(
            proxy.resource_aware_model_manager._load_waiter  # noqa: SLF001 - private hook
        )
        logger.info("✅ ModelRouter wired with load_waiter for event-driven eviction")

        # Wire up model sync callback for coordinator state sync
        # CRITICAL: Do this BEFORE connecting to gateway
        coordinator = proxy.resource_aware_model_manager._global_load_coordinator  # noqa: SLF001
        model_sync_callback = _build_loaded_model_sync_callback(coordinator)
        proxy.gateway_manager.set_model_sync_callback(model_sync_callback)

        # NOW connect to gateway - coordinator executor is running and ready
        logger.info("🔌 Connecting to gateway (coordinator ready)...")
        try:
            await asyncio.wait_for(proxy.gateway_manager.initialize(), timeout=10.0)
            logger.info("✅ Gateway connection established")
        except TimeoutError:
            logger.warning("⚠️ Gateway connection timed out after 10 seconds")
            gateway = proxy.gateway_manager.get_gateway()
            if gateway:
                logger.info("✅ Continuing with healthy gateway")
            else:
                logger.error(
                    "❌ Gateway not available after timeout, attempting reconnect later"
                )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("❌ Gateway connection failed: %s", exc, exc_info=True)
            logger.warning(
                "⚠️ Stargate will start without gateways and attempt reconnect"
            )

        healthy_gateway = proxy.gateway_manager.get_gateway()
        if not healthy_gateway:
            logger.warning("⚠️ No healthy gateway available at startup")
            logger.info(
                "🔄 Stargate starting in degraded mode - probing gateway during runtime"
            )
        else:
            logger.info("✅ Starting with healthy gateway")

            # Register default resource managers if gateways.yaml doesn't exist
            config_path = Path("config/gateways.yaml")
            if not config_path.exists():
                await proxy.resource_aware_model_manager.register_default_resource_managers_after_connection()

        # Sync currently connected gateways' loaded models to coordinator
        # (handles models loaded during initial connection before callback
        # was registered)
        _sync_all_gateway_loaded_models(proxy.gateway_manager, model_sync_callback)

    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error(
            "❌ Failed to initialize resource-aware model manager: %s",
            exc,
            exc_info=True,
        )
        logger.warning("⚠️ Stargate starting without resource-aware routing.")
        proxy.resource_aware_model_manager = None


def _sync_all_gateway_loaded_models(
    gateway_manager: SingleGatewayManager,
    model_sync_callback,
) -> None:
    """
    Sync local gateway's loaded models to coordinator.

    Called after ResourceAwareModelManager initialization to ensure
    coordinator knows about models loaded during initial WebSocket connection.

    Pre: gateway_manager.gateway initialized
    Post: ∀ model ∈ gateway.loaded_models ⟹
          coordinator.where_is_loaded(model) = gateway
    """
    gateway = gateway_manager.get_gateway()
    if not gateway:
        logger.debug("No gateway available for model sync")
        return

    loaded_models = gateway.client.get_loaded_models()
    # Always sync (even if empty) to clear stale state
    model_sync_callback(gateway.config.name, loaded_models)

    logger.info(
        "✅ Synced %d model(s) from gateway to coordinator",
        len(loaded_models),
    )
