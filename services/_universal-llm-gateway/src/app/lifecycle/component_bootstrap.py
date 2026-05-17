"""Core gateway component construction for the lifecycle package.

Owns loading configs, creating EventBus, debug broadcaster, model registry,
metadata adapter, worker controller, global resource tracker event bus, and
resource monitor. This isolates the heavy component wiring from the FastAPI
lifespan orchestration.
"""

import os
import socket

from ...core.config_loader import ConfigLoader
from ...core.events import (
    EventBus,
    MinimalEventDebugBroadcaster,
    set_event_bus,
)
from ...core.gateway_config import GatewayConfig
from ...core.model_registry import ModelRegistry
from ...core.resource_monitor import ResourceMonitor
from ...core.resources import resource_tracker
from ...core.workers import WorkerController
from ...core.workers.process.communication import register_cleanup_event_handlers
from ...routers.model_metadata_adapter import ModelMetadataAdapter
from .logging_bootstrap import get_gateway_logger

# Socket path defaults. These are candidates for centralization into a
# transport_utils module if one is introduced in the future. For now they
# live here to avoid scattering magic strings while preserving original
# behavior and environment variable overrides.
DEFAULT_EVENT_DEBUG_SOCKET = "/tmp/universal-llm-gateway-events.sock"
DEFAULT_EVENTS_INGEST_SOCK = "/tmp/universal-protocol/events.sock"


async def initialize_components(
    config_loader: ConfigLoader,
) -> tuple[
    EventBus,
    ModelRegistry,
    ModelMetadataAdapter,
    WorkerController,
    GatewayConfig,
    ResourceMonitor,
]:
    """Initialize all core gateway components in the prescribed order.

    This function performs the heavy lifting of:
    - Loading gateway and models configuration
    - Creating the central EventBus and wiring the debug broadcaster
    - Initializing ModelRegistry, ModelMetadataAdapter, and WorkerController
    - Attaching the EventBus to the global resource tracker
    - Creating the ResourceMonitor for state streaming

    Logging is performed via the lifecycle gateway logger (initialized before
    this function is called by the lifespan orchestrator).

    The original intra-function imports for set_event_bus and
    register_cleanup_event_handlers have been converted to package-relative
    top-level imports using the `...` form required for code at
    src/app/lifecycle/ depth.

    Returns the same 6-tuple as the original monolithic implementation so that
    call sites require no changes.
    """
    gateway_logger = get_gateway_logger()

    # Load configurations
    gateway_config, models_config, _ = config_loader.load_all_configs()

    # Initialize event bus (central event distribution system)
    event_bus = EventBus()

    # Set global event bus for modules that need it
    set_event_bus(event_bus)

    # Register cleanup event handlers
    register_cleanup_event_handlers()

    # Attach debug broadcaster with persistence for post-mortem debugging
    socket_path = os.getenv("EVENT_DEBUG_SOCKET", DEFAULT_EVENT_DEBUG_SOCKET)

    debug_broadcaster = MinimalEventDebugBroadcaster(
        socket_path=socket_path,
        uds_publish_path=os.environ.get(
            "EVENTS_INGEST_SOCK", DEFAULT_EVENTS_INGEST_SOCK
        ),
    )
    event_bus.set_debug_broadcaster(debug_broadcaster)

    # Start debug server for live debug socket + event service publishing
    await debug_broadcaster.start_debug_server()

    if gateway_logger is not None:
        gateway_logger.info(
            "EventBus initialized with debug broadcasting and event service publishing"
        )
        gateway_logger.info(f"  Socket: {socket_path}")

    # Initialize model registry
    model_registry = ModelRegistry(models_config)

    # Initialize model metadata adapter
    model_metadata_adapter = ModelMetadataAdapter(model_registry, gateway_config)

    # Initialize the worker controller with integrated resource tracking and EventBus
    worker_controller = WorkerController(model_registry, gateway_config, event_bus)

    # Set EventBus on global resource tracker
    resource_tracker.set_event_bus(event_bus)

    # Initialize resource monitor for state streaming
    gateway_name = os.environ.get("GATEWAY_NAME", socket.gethostname())
    resource_monitor = ResourceMonitor(event_bus, gateway_name)
    if gateway_logger is not None:
        gateway_logger.info(
            f"Resource monitor initialized, gateway_name={gateway_name}"
        )

    return (
        event_bus,
        model_registry,
        model_metadata_adapter,
        worker_controller,
        gateway_config,
        resource_monitor,
    )
