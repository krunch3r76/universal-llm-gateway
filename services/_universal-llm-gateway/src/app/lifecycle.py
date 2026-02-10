"""Application lifecycle management for Universal LLM Gateway."""

import os
import socket
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

try:
    from universal_logging import get_logger

    from .. import __version__
    from ..core.config_loader import ConfigLoader, get_config_loader
    from ..core.config_manager import ConfigManager
    from ..core.events import EventBus, MinimalEventDebugBroadcaster
    from ..core.hot_reload import HotReloadManager
    from ..core.model_registry import ModelRegistry
    from ..core.resource_monitor import ResourceMonitor
    from ..core.resources import resource_tracker
    from ..core.workers import WorkerController, set_worker_controller
    from ..core.workers.orphan_detector import OrphanedSocketDetector
    from ..core.workers.resource_tracker_crash_handler import (
        ResourceTrackerCrashHandler,
    )
    from ..core.workers.stream_cancellation_handler import StreamCancellationHandler
    from ..routers.model_metadata_adapter import ModelMetadataAdapter
except ImportError:
    # When running directly, use absolute imports
    from universal_logging import get_logger

    from src import __version__
    from src.core.config_loader import ConfigLoader, get_config_loader
    from src.core.config_manager import ConfigManager
    from src.core.events import EventBus, MinimalEventDebugBroadcaster
    from src.core.hot_reload import HotReloadManager
    from src.core.model_registry import ModelRegistry
    from src.core.resource_monitor import ResourceMonitor
    from src.core.resources import resource_tracker
    from src.core.workers import WorkerController, set_worker_controller
    from src.core.workers.orphan_detector import OrphanedSocketDetector
    from src.core.workers.resource_tracker_crash_handler import (
        ResourceTrackerCrashHandler,
    )
    from src.core.workers.stream_cancellation_handler import (
        StreamCancellationHandler,
    )
    from src.routers.model_metadata_adapter import ModelMetadataAdapter

# Loggers initialized after logging setup (see lifespan function)
# This prevents premature auto-initialization with wrong paths
logger = None
gateway_logger = None


async def graceful_shutdown(
    app: FastAPI,
    timeout: float = 30.0,
) -> None:
    """
    Graceful shutdown using event-driven idle detection.

    Emits GATEWAY_DRAINING event, then waits for worker_controller to
    become idle via callback notification. Falls back to timeout.

    REFACTORED: Uses asyncio.Event.wait() instead of polling loop.

    Args:
        app: FastAPI application with worker_controller in state
        timeout: Maximum seconds to wait for idle
    """
    import asyncio

    gateway_name = os.environ.get("GATEWAY_NAME", socket.gethostname())

    # Emit GATEWAY_DRAINING event
    try:
        if hasattr(app.state, "event_bus") and app.state.event_bus:
            from ..core.events.types import GatewayDraining

            drain_event = GatewayDraining(
                gateway_id=gateway_name,
                reason="graceful_shutdown",
                timeout=timeout,
                timestamp=time.time(),
            )
            await app.state.event_bus.publish_async_nowait(drain_event)
            if gateway_logger is not None:
                gateway_logger.info(
                    f"Emitted GATEWAY_DRAINING event (timeout={timeout}s)"
                )
    except Exception as e:
        if gateway_logger is not None:
            gateway_logger.warning(f"Failed to emit drain event: {e}")

    # Event-driven idle detection
    worker_controller = getattr(app.state, "worker_controller", None)
    if not worker_controller:
        if gateway_logger is not None:
            gateway_logger.info("No worker controller, proceeding with shutdown")
        return

    # Create event for idle notification
    idle_event = asyncio.Event()

    # Register callback to set event when idle
    async def on_idle_callback():
        idle_event.set()

    # Register callback BEFORE checking idle state to avoid race condition:
    # If we check is_idle() first and it returns False, then inference completes
    # before we register the callback, _notify_idle_if_needed() would be called
    # with an empty callback list and we'd never be notified.
    if hasattr(worker_controller, "register_idle_callback"):
        worker_controller.register_idle_callback(on_idle_callback)
    else:
        # Fallback: no callback support, just wait with timeout
        if gateway_logger is not None:
            gateway_logger.warning(
                "WorkerController lacks idle callback support, using timeout only"
            )
        await asyncio.sleep(timeout)
        if gateway_logger is not None:
            gateway_logger.warning(
                f"Graceful shutdown timeout ({timeout}s), forcing shutdown"
            )
        return

    # Check if already idle AFTER registering callback
    if hasattr(worker_controller, "is_idle") and worker_controller.is_idle():
        if gateway_logger is not None:
            gateway_logger.info("Gateway already idle, proceeding with shutdown")
        return

    # Wait for idle or timeout
    try:
        await asyncio.wait_for(idle_event.wait(), timeout=timeout)
        if gateway_logger is not None:
            gateway_logger.info("Gateway became idle, proceeding with shutdown")
    except TimeoutError:
        if gateway_logger is not None:
            gateway_logger.warning(
                f"Graceful shutdown timeout ({timeout}s), forcing shutdown"
            )


def setup_logging_from_config(config_loader: ConfigLoader) -> None:
    """Setup logging by loading and applying logging.yaml configuration.

    Note: LOG_DIR environment variable should already be set by main.py
    before any imports to prevent early logger initialization issues.
    """
    try:
        log_dir = os.getenv("LOG_DIR")
        if not log_dir:
            # Fallback if somehow not set (should never happen in production)
            data_dir = os.getenv("DATA_DIR", "/tmp")
            log_dir = os.path.join(data_dir, "logs", "universal-llm-gateway")
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            os.environ["LOG_DIR"] = log_dir
            print(f"[LOGGING] Warning: LOG_DIR not set, using fallback: {log_dir}")

        # Load logging configuration from YAML
        logging_config = config_loader.load_logging_config()

        if logging_config:
            # Expand environment variables (${VAR:-default} syntax) in ALL values.
            # yaml.safe_load doesn't expand env vars, so ${LOG_LEVEL:-INFO} would be
            # passed as a literal string, causing "Unable to configure handler" errors
            from universal_logging.config_discovery import expand_env_vars

            logging_config = expand_env_vars(logging_config)

            # Apply configuration with automatic truncation support
            from universal_logging import setup

            setup(logging_config)

            print(f"[LOGGING] Configuration from YAML applied to {log_dir}")
        else:
            print(
                f"[LOGGING] No YAML config found, "
                f"universal_logging auto-initialized to {log_dir}"
            )

    except Exception as e:
        # Fallback setup - still let universal_logging handle it
        print(
            f"[LOGGING] Warning: setup failed ({e}), "
            "universal_logging will use emergency fallback"
        )


async def initialize_components(
    config_loader: ConfigLoader,
) -> tuple[
    EventBus,
    ModelRegistry,
    ModelMetadataAdapter,
    WorkerController,
    Any,  # GatewayConfig
    ResourceMonitor,
]:
    """Initialize all components including the event bus"""
    # Load configurations
    gateway_config, models_config, _ = config_loader.load_all_configs()

    # Initialize event bus (central event distribution system)
    event_bus = EventBus()

    # Set global event bus for modules that need it
    from ..core.events import set_event_bus

    set_event_bus(event_bus)

    # Register cleanup event handlers
    from ..core.workers.process.communication import register_cleanup_event_handlers

    register_cleanup_event_handlers()

    # Attach debug broadcaster with persistence for post-mortem debugging
    socket_path = os.getenv(
        "EVENT_DEBUG_SOCKET", "/tmp/universal-llm-gateway-events.sock"
    )

    # Event persistence (JSONL files)
    persistence_config = {
        "enabled": True,
        "directory": "/tmp/_universal-gateway-events",
        "max_file_size_mb": 50,
        "max_files": 3,
        "flush_interval_seconds": 1.0,
    }

    debug_broadcaster = MinimalEventDebugBroadcaster(
        socket_path=socket_path,
        persistence_config=persistence_config,
    )
    event_bus.set_debug_broadcaster(debug_broadcaster)

    # Start debug server to enable event persistence
    await debug_broadcaster.start_debug_server()

    gateway_logger.info(
        "EventBus initialized with debug broadcasting and event persistence"
    )
    gateway_logger.info(f"  Socket: {socket_path}")
    gateway_logger.info(f"  Events: {persistence_config['directory']}/current.jsonl")

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
    gateway_logger.info(f"Resource monitor initialized, gateway_name={gateway_name}")

    return (
        event_bus,
        model_registry,
        model_metadata_adapter,
        worker_controller,
        gateway_config,
        resource_monitor,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:  # noqa: PLR0912, PLR0915
    """Application lifespan management with async model operations"""

    try:
        config_loader = get_config_loader()

        # CRITICAL: Setup logging FIRST before any logging calls
        # This prevents universal_logging auto-initialization
        # from overriding YAML config
        setup_logging_from_config(config_loader)

        # Initialize loggers AFTER logging configuration is applied
        global logger, gateway_logger
        logger = get_logger(__name__)
        gateway_logger = get_logger("universal_llm_gateway.main")

        # Startup logging (now with proper configuration applied)
        gateway_logger.info("Universal LLM Gateway starting up")

        # Initialize components (now async)
        (
            event_bus,
            model_registry,
            model_metadata_adapter,
            worker_controller,
            gateway_config,
            resource_monitor,
        ) = await initialize_components(config_loader)

        # Store components in app.state for dependency injection
        app.state.event_bus = event_bus
        app.state.model_registry = model_registry
        app.state.model_metadata_adapter = model_metadata_adapter
        app.state.worker_controller = worker_controller
        set_worker_controller(worker_controller)  # Enable module-level access for jobs
        app.state.gateway_config = gateway_config
        app.state.resource_monitor = resource_monitor
        app.state.resource_tracker = resource_tracker

        # Validate models (can be skipped for faster startup)
        # NOTE: Validation now always runs regardless of this setting to prevent
        # gateways from advertising models they cannot load (prevents routing failures)
        enable_availability_check = (
            os.getenv("ENABLE_MODEL_AVAILABILITY_CHECK", "true").lower() == "true"
        )
        # CRITICAL FIX: Always validate model files to prevent advertising
        # missing models. Ensures gateways advertise loadable models only.
        validation_report = model_registry.validate_model_files(fast_mode=True)

        if not enable_availability_check:
            gateway_logger.warning(
                "Model availability check was configured as disabled, but validation "
                "still runs to prevent advertising missing model files. "
                f"Validation completed: valid={validation_report.valid_models}/"
                f"{validation_report.total_models} models"
            )
            if validation_report.valid_models < validation_report.total_models:
                invalid_count = (
                    validation_report.total_models - validation_report.valid_models
                )
                gateway_logger.warning(
                    f"Model validation issues detected: "
                    f"valid={validation_report.valid_models}, "
                    f"total={validation_report.total_models}, "
                    f"invalid={invalid_count}"
                )

        # Validate profile resources (critical for eviction to work)
        from src.core.model_registry.validation import ModelValidator

        models_config = model_registry.model_loaders_config.get("models", {})
        profile_issues = ModelValidator.validate_profile_resources(models_config)
        ModelValidator.log_profile_validation_results(profile_issues)

        # Start worker controller
        await worker_controller.start()

        # Clean up orphaned workers from previous gateway instance
        # Ensures Stargate coordinator state stays in sync with actual workers
        try:
            orphan_detector = OrphanedSocketDetector(worker_controller)
            socket_dir = Path("/tmp/universal-protocol")
            orphaned = await orphan_detector.find_orphaned_sockets(socket_dir)

            if orphaned:
                gateway_logger.info(
                    f"🧹 Found {len(orphaned)} orphaned worker(s) from "
                    f"previous gateway instance, cleaning up..."
                )
                for model_id, socket_path, reason in orphaned:
                    gateway_logger.info(
                        f"🧹 Cleaning up orphaned worker: {model_id} (reason: {reason})"
                    )
                    try:
                        from ..core.events.types import ModelUnloaded

                        # Broadcast MODEL_UNLOADED to notify Stargate
                        await event_bus.publish_async_nowait(
                            ModelUnloaded(model_id=model_id)
                        )

                        # Kill orphaned process and clean up socket
                        cleanup_fn = worker_controller._cleanup_socket_file
                        await worker_controller._lifecycle_manager.fallback_process_cleanup(  # noqa: E501
                            model_id, cleanup_fn
                        )
                        gateway_logger.info(
                            f"✅ Cleaned up orphaned worker: {model_id}"
                        )
                    except Exception as cleanup_err:
                        gateway_logger.warning(
                            f"Failed to clean up orphaned worker "
                            f"{model_id}: {cleanup_err}"
                        )
            else:
                gateway_logger.debug("No orphaned workers detected on startup")
        except Exception as e:
            gateway_logger.warning(f"Orphan detection failed on startup: {e}")

        # Initialize minimal event-driven crash handling
        # Bridge process_ipc crash events to gateway crash events
        from src.core.workers.process_crash_bridge import ProcessCrashBridge

        process_crash_bridge = ProcessCrashBridge(event_bus)

        # Handle gateway crash events and update resource tracker
        resource_tracker_crash_handler = ResourceTrackerCrashHandler(
            event_bus, worker_controller
        )

        # Initialize stream cancellation handler to mark models idle on stream cancel
        stream_cancellation_handler = StreamCancellationHandler(event_bus)

        # Store minimal handlers in app state
        app.state.process_crash_bridge = process_crash_bridge
        app.state.resource_tracker_crash_handler = resource_tracker_crash_handler
        app.state.stream_cancellation_handler = stream_cancellation_handler

        # Let the OS handle socket cleanup - no manual health monitoring needed

        valid_count = validation_report.valid_models if validation_report else "skipped"
        gateway_logger.info(
            f"Universal LLM Gateway startup completed: "
            f"version={__version__}, "
            f"models_loaded={len(model_registry.models_to_metadata)}, "
            f"models_valid={valid_count}, "
            f"phase=2-Process_Isolation_Architecture"
        )

        # Initialize hot reload manager if enabled
        hot_reload_manager = None
        if gateway_config.hot_reload.enabled:
            try:
                # Create configuration manager for hot reload
                config_manager = ConfigManager(
                    gateway_config.model_registry.config_file
                )

                # Initialize hot reload manager
                hot_reload_manager = HotReloadManager(
                    config_manager=config_manager,
                    model_registry=model_registry,
                    watch_directory=gateway_config.hot_reload.watch_directory,
                    debounce_ms=gateway_config.hot_reload.debounce_ms,
                    recursive=gateway_config.hot_reload.recursive,
                    supported_formats=gateway_config.hot_reload.supported_formats,
                    log_level=gateway_config.hot_reload.log_level,
                )

                # Register callback to emit CATALOG_RELOADED on config changes
                async def emit_catalog_reloaded(reload_event):
                    """Emit CATALOG_RELOADED event after successful hot reload."""
                    from ..core.events.types import CatalogReloaded

                    if reload_event.success:
                        catalog_event = CatalogReloaded(
                            reason=f"hot_reload:{reload_event.file_path}",
                        )
                        await event_bus.publish_async_nowait(catalog_event)
                        gateway_logger.info(
                            f"Emitted CATALOG_RELOADED for {reload_event.model_key}"
                        )

                hot_reload_manager.add_reload_callback(emit_catalog_reloaded)

                # Start hot reload monitoring
                if await hot_reload_manager.start():
                    app.state.hot_reload_manager = hot_reload_manager
                    gateway_logger.info(
                        f"Hot reload monitoring started: "
                        f"watch_directory={gateway_config.hot_reload.watch_directory}, "
                        f"debounce_ms={gateway_config.hot_reload.debounce_ms}, "
                        f"recursive={gateway_config.hot_reload.recursive}"
                    )
                else:
                    gateway_logger.error("Failed to start hot reload monitoring")
                    hot_reload_manager = None

            except Exception as e:
                gateway_logger.error(
                    f"Failed to initialize hot reload manager: {e}", exc_info=True
                )
                hot_reload_manager = None
        else:
            gateway_logger.info("Hot reload disabled in configuration")

        # Event-driven crash detection - no health monitoring needed
        # process_ipc handles internal health monitoring and publishes events
        gateway_logger.info(
            "Event-driven crash detection enabled (no health monitoring)"
        )

        # Initialize and start WebSocket rate limiter
        try:
            from ..middleware.rate_limiter import websocket_rate_limiter

            await websocket_rate_limiter.start()

            gateway_logger.info("WebSocket rate limiter started")
        except Exception as e:
            gateway_logger.warning(f"Failed to start WebSocket services: {e}")

        # Start state channel metrics collector
        try:
            from ..core.metrics.state_channel_metrics import state_channel_metrics

            await state_channel_metrics.start()
            gateway_logger.info("State channel metrics collector started")
        except Exception as e:
            gateway_logger.warning(f"Failed to start state channel metrics: {e}")

        # Initialize WebSocket event forwarder for Stargate control plane
        try:
            from .websocket_lifecycle import initialize_websocket_forwarder

            await initialize_websocket_forwarder(app)
        except Exception as e:
            # Use fallback if logger not initialized yet
            if gateway_logger is not None:
                gateway_logger.warning(
                    f"Failed to start WebSocket event forwarder: {e}"
                )
            else:
                print(f"WARNING: Failed to start WebSocket event forwarder: {e}")

    except Exception as e:
        # Log the error with full stack trace
        # Use fallback if logger not initialized yet (early startup failure)
        if gateway_logger is not None:
            gateway_logger.error(
                f"Failed to start Universal LLM Gateway: {e}", exc_info=True
            )
        else:
            print(f"ERROR: Failed to start Universal LLM Gateway: {e}")
            import traceback

            traceback.print_exc()
        # Re-raise to preserve the original stack trace
        raise

    # DIAGNOSTIC: Log before yield
    if gateway_logger is not None:
        gateway_logger.error(
            "🚨 GATEWAY LIFESPAN: About to yield (entering application run phase)"
        )

    yield

    # DIAGNOSTIC: Log after yield (this means shutdown was initiated)
    if gateway_logger is not None:
        gateway_logger.error("🚨 GATEWAY LIFESPAN: Exited yield - shutdown initiated!")
        import traceback

        gateway_logger.error(
            f"🚨 Call stack at yield exit:\n{''.join(traceback.format_stack())}"
        )

    # Shutdown
    if gateway_logger is not None:
        gateway_logger.info("Universal LLM Gateway shutting down")

    # Check shutdown mode
    shutdown_mode = os.environ.get("GATEWAY_SHUTDOWN_MODE", "fast")

    if shutdown_mode == "graceful":
        graceful_timeout = float(os.environ.get("GATEWAY_GRACEFUL_TIMEOUT", "30"))
        if gateway_logger is not None:
            gateway_logger.info(f"Graceful shutdown mode (timeout={graceful_timeout}s)")
        await graceful_shutdown(app, timeout=graceful_timeout)

    # Emit GATEWAY_SHUTDOWN event (always, after graceful if applicable)
    try:
        gateway_name = os.environ.get("GATEWAY_NAME", socket.gethostname())
        if hasattr(app.state, "event_bus") and app.state.event_bus:
            import asyncio

            from ..core.events.types import GatewayShutdown

            shutdown_event = GatewayShutdown(
                gateway_id=gateway_name,
                reason="shutdown",
                timestamp=time.time(),
            )
            # True fire-and-forget: schedule but don't await
            asyncio.create_task(
                app.state.event_bus.publish_async_nowait(shutdown_event)
            )
            if gateway_logger is not None:
                gateway_logger.info(
                    f"Emitted GATEWAY_SHUTDOWN event for {gateway_name}"
                )
    except Exception as e:
        if gateway_logger is not None:
            gateway_logger.warning(f"Failed to emit shutdown event: {e}")

    try:
        # Event-driven crash detection - no manual cleanup needed

        # Shutdown WebSocket rate limiter
        try:
            from ..middleware.rate_limiter import websocket_rate_limiter

            await websocket_rate_limiter.shutdown()
            if gateway_logger is not None:
                gateway_logger.info("WebSocket rate limiter shutdown completed")
        except Exception as e:
            if gateway_logger is not None:
                gateway_logger.warning(f"Error shutting down rate limiter: {e}")

        # Shutdown state channel metrics collector
        try:
            from ..core.metrics.state_channel_metrics import state_channel_metrics

            await state_channel_metrics.stop()
            if gateway_logger is not None:
                gateway_logger.info(
                    "State channel metrics collector shutdown completed"
                )
        except Exception as e:
            if gateway_logger is not None:
                gateway_logger.warning(
                    f"Error shutting down state channel metrics: {e}"
                )

        # Shutdown WebSocket event forwarder
        try:
            from .websocket_lifecycle import shutdown_websocket_forwarder

            await shutdown_websocket_forwarder(app)
        except Exception as e:
            if gateway_logger is not None:
                gateway_logger.warning(
                    f"Error shutting down WebSocket event forwarder: {e}"
                )

        # Shutdown debug broadcaster (must happen before EventBus shutdown)
        try:
            if hasattr(app.state, "event_bus") and app.state.event_bus:
                debug_broadcaster = app.state.event_bus.debug_broadcaster
                if debug_broadcaster:
                    await debug_broadcaster.stop_debug_server()
                    if gateway_logger is not None:
                        gateway_logger.info("Debug event broadcaster stopped")
        except Exception as e:
            if gateway_logger is not None:
                gateway_logger.warning(f"Error shutting down debug broadcaster: {e}")

        # Cleanup worker controller and stop all worker processes
        if hasattr(app.state, "worker_controller") and app.state.worker_controller:
            # WorkerController (process isolation architecture)
            await app.state.worker_controller.stop()
            if gateway_logger is not None:
                gateway_logger.info("Worker processes shut down")

        # Stop hot reload monitoring
        if hasattr(app.state, "hot_reload_manager") and app.state.hot_reload_manager:
            await app.state.hot_reload_manager.stop()
            if gateway_logger is not None:
                gateway_logger.info("Hot reload monitoring stopped")

        if gateway_logger is not None:
            gateway_logger.info("Universal LLM Gateway shutdown completed")

    except Exception as e:
        if gateway_logger is not None:
            gateway_logger.error(f"Error during shutdown: {e}", exc_info=True)


# Health monitoring removed - using pure event-driven detection
# process_ipc handles all process monitoring and publishes events
