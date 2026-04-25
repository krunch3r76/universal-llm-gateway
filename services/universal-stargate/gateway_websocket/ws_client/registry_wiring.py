"""Handler registry wiring for WebSocket message dispatch."""

from collections.abc import Awaitable, Callable
from typing import Any

from universal_logging import get_logger

from ..event import ws_url_to_http
from ..handler import (
    CatalogUpdateHandler,
    ComputeCapacityTelemetryHandler,
    ComputeQueueAcquiredHandler,
    ComputeQueueWaitHandler,
    ErrorHandler,
    GatewayDrainingHandler,
    GatewayShutdownHandler,
    HandlerContext,
    HandlerRegistry,
    ModelBusyHandler,
    ModelIdleHandler,
    ModelLoadedHandler,
    ModelLoadFailedHandler,
    ModelLoadingStartedHandler,
    ModelUnloadedHandler,
    PingHandler,
    QueryResponseHandler,
    RequestInferenceStartedHandler,
    ResourceUpdateHandler,
    TelemetryHeartbeatHandler,
)
from ..messages import MessageType
from .events import EventPublisher
from .queries import QueryManager
from .state import GatewayState

logger = get_logger(__name__)


def create_handler_registry() -> HandlerRegistry:
    """
    Create and populate handler registry.

    Maps MessageType to handler instances.
    Verifies coverage of expected message types on startup.

    Returns:
        Configured HandlerRegistry with all message handlers registered
    """
    registry = HandlerRegistry()

    # Model loading lifecycle
    registry.register_sync(
        MessageType.MODEL_LOADING_STARTED, ModelLoadingStartedHandler()
    )
    registry.register_sync(MessageType.MODEL_LOADED, ModelLoadedHandler())
    registry.register_sync(MessageType.MODEL_LOAD_FAILED, ModelLoadFailedHandler())
    registry.register_sync(MessageType.MODEL_BUSY, ModelBusyHandler())

    # Model availability
    registry.register_sync(MessageType.MODEL_IDLE, ModelIdleHandler())
    registry.register_sync(MessageType.MODEL_UNLOADED, ModelUnloadedHandler())

    # System events
    registry.register_async(MessageType.PING, PingHandler())
    registry.register_sync(MessageType.RESOURCE_UPDATE, ResourceUpdateHandler())
    registry.register_sync(MessageType.ERROR, ErrorHandler())
    registry.register_sync(MessageType.GATEWAY_SHUTDOWN, GatewayShutdownHandler())
    registry.register_sync(MessageType.GATEWAY_DRAINING, GatewayDrainingHandler())

    # Catalog and query
    registry.register_sync(MessageType.CATALOG_UPDATE, CatalogUpdateHandler())
    registry.register_sync(MessageType.RESPONSE, QueryResponseHandler())

    # Heartbeat
    registry.register_sync(MessageType.TELEMETRY_HEARTBEAT, TelemetryHeartbeatHandler())

    # Compute capacity telemetry (orchestration observability)
    registry.register_sync(MessageType.COMPUTE_QUEUE_WAIT, ComputeQueueWaitHandler())
    registry.register_sync(
        MessageType.COMPUTE_QUEUE_ACQUIRED, ComputeQueueAcquiredHandler()
    )
    registry.register_sync(
        MessageType.REQUEST_INFERENCE_STARTED, RequestInferenceStartedHandler()
    )

    # Verify coverage (log warning on startup if missing handlers)
    expected = {
        MessageType.MODEL_LOADING_STARTED,
        MessageType.MODEL_LOADED,
        MessageType.MODEL_LOAD_FAILED,
        MessageType.MODEL_UNLOADED,
        MessageType.MODEL_BUSY,
        MessageType.MODEL_IDLE,
        MessageType.RESOURCE_UPDATE,
        MessageType.CATALOG_UPDATE,
        MessageType.GATEWAY_SHUTDOWN,
        MessageType.GATEWAY_DRAINING,
        MessageType.PING,
        MessageType.RESPONSE,
        MessageType.ERROR,
        MessageType.TELEMETRY_HEARTBEAT,
        MessageType.COMPUTE_QUEUE_WAIT,
        MessageType.COMPUTE_QUEUE_ACQUIRED,
        MessageType.REQUEST_INFERENCE_STARTED,
    }
    missing = registry.verify_coverage(expected)
    if missing:
        logger.warning(f"Missing handlers for message types: {missing}")

    return registry


def build_handler_context(
    state: GatewayState,
    event_publisher: EventPublisher,
    query_manager: QueryManager,
    ws_url: str,
    gateway_name: str,
    send_message: Callable[[str], Awaitable[None]] | None,
    schedule_callback: Callable[
        [Callable[..., Awaitable[None]], tuple[object, ...]],
        None,
    ],
    on_model_loading_started: Callable[[str], Awaitable[None]] | None,
    on_model_loaded: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
    on_model_unloaded: Callable[[str], Awaitable[None]] | None,
    on_model_load_failed: (
        Callable[
            [str, str, dict[str, Any] | None, dict[str, Any] | None], Awaitable[None]
        ]
        | None
    ),
    on_model_busy: Callable[[str], Awaitable[None]] | None,
    on_model_idle: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
    on_resource_update: Callable[[dict[str, Any]], Awaitable[None]] | None,
    on_catalog_update: Callable[[dict[str, Any]], Awaitable[None]] | None,
    on_telemetry_heartbeat: Callable[[dict[str, Any]], Awaitable[None]] | None,
    on_request_inference_started: (
        Callable[[str, str, str, str | None], Awaitable[None]] | None
    ),
    model_loaded_callbacks: dict[
        str, set[Callable[[str, dict[str, Any]], Awaitable[None]]]
    ]
    | None = None,
    model_load_failed_callbacks: dict[str, set[Callable[[str, str], Awaitable[None]]]]
    | None = None,
) -> HandlerContext:
    """
    Build context for message handlers.

    Provides handlers with:
    - Mutable state references for direct mutation
    - Side-effect schedulers (fire-and-forget)
    - Gateway metadata

    Args:
        state: Gateway state container
        event_publisher: Event bus publisher
        query_manager: Query/response manager
        ws_url: WebSocket URL
        gateway_name: Gateway name
        send_message: WebSocket send function (for PING→PONG)
        schedule_callback: Fire-and-forget callback scheduler
        on_model_loading_started: Callback for MODEL_LOADING_STARTED
        on_model_loaded: Callback for MODEL_LOADED
        on_model_unloaded: Callback for MODEL_UNLOADED
        on_model_load_failed: Callback for MODEL_LOAD_FAILED
        on_model_busy: Callback for MODEL_BUSY
        on_model_idle: Callback for MODEL_IDLE
        on_resource_update: Callback for RESOURCE_UPDATE
        on_catalog_update: Callback for CATALOG_UPDATE
        on_telemetry_heartbeat: Callback for TELEMETRY_HEARTBEAT
        on_request_inference_started: Callback for REQUEST_INFERENCE_STARTED
        model_loaded_callbacks: Per-model loaded callbacks for load trackers
        model_load_failed_callbacks: Per-model load-failed callbacks for trackers

    Returns:
        HandlerContext configured with all capabilities
    """

    # Heartbeat callback
    async def on_heartbeat() -> None:
        """Update heartbeat timestamp."""
        state.update_heartbeat_timestamp()

    # Resource change callback
    async def on_resource_change() -> None:
        """Update resource timestamp when capacity changes."""
        state.update_resource_timestamp()

    # Create capacity telemetry handler for this gateway
    capacity_telemetry_handler = ComputeCapacityTelemetryHandler(
        gateway_id=gateway_name
    )

    return HandlerContext(
        # State references (mutable)
        loaded_models=state.loaded_models,
        loading_models=state.loading_models,
        busy_models=state.busy_models,
        busy_since=state.busy_since,
        loading_since=state.loading_since,
        models=state.models,
        catalog=state.catalog,
        model_last_inference=state.model_last_inference,
        model_details=state.model_details,
        measured_model_vram=state.measured_model_vram,
        # Resources (reservation-aware update via setter)
        _resources=state.resources,
        _resources_from_gateway_setter=state.update_resources_from_gateway,
        # Metadata
        gateway_name=gateway_name,
        gateway_http_url=ws_url_to_http(ws_url),
        # Side-effect schedulers
        schedule_callback=schedule_callback,
        schedule_capacity_freed=event_publisher.schedule_capacity_freed,
        schedule_model_loading_started=event_publisher.schedule_model_loading_started,
        schedule_model_loaded=event_publisher.schedule_model_loaded,
        schedule_model_load_failed=event_publisher.schedule_model_load_failed,
        capture_gateway_state_snapshot=event_publisher.capture_gateway_state_snapshot,
        # I/O
        send_message=send_message,
        # Query handling
        pending_queries=query_manager.pending_queries,
        # Callbacks (global)
        on_model_loading_started=on_model_loading_started,
        on_model_loaded=on_model_loaded,
        on_model_unloaded=on_model_unloaded,
        on_model_load_failed=on_model_load_failed,
        on_model_busy=on_model_busy,
        on_model_idle=on_model_idle,
        on_resource_update=on_resource_update,
        on_catalog_update=on_catalog_update,
        on_heartbeat=on_heartbeat,
        on_resource_change=on_resource_change,
        on_telemetry_heartbeat=on_telemetry_heartbeat,
        on_request_inference_started=on_request_inference_started,
        on_vram_drift=event_publisher.schedule_vram_drift,
        can_report_vram_drift=state.can_report_vram_drift,
        # Model-specific callbacks (for concurrent load tracking)
        # CRITICAL: Pass dict by reference, not copy - use `is None` check
        model_loaded_callbacks=model_loaded_callbacks
        if model_loaded_callbacks is not None
        else {},
        model_load_failed_callbacks=model_load_failed_callbacks
        if model_load_failed_callbacks is not None
        else {},
        # Telemetry handler (per-gateway capacity telemetry)
        _capacity_telemetry_handler=capacity_telemetry_handler,
    )
