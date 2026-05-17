"""FastAPI lifespan context manager orchestration.

This module is the sole public surface of the lifecycle package. It contains
only the thin `lifespan` async context manager that sequences the other
specialized modules:

    logging_bootstrap -> component_bootstrap -> model_validation_startup
    -> worker_runtime_startup -> hot_reload_runtime -> edge_service_runtime
    (then yield to app) -> shutdown_sequence on exit.

All heavy lifting, side effects, and ordering details live in the sibling
modules. The re-export from __init__.py ensures that `from .lifecycle import
lifespan` and `from src.app.lifecycle import lifespan` continue to work.
"""

import traceback
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ... import __version__
from ...core.config_loader import get_config_loader
from ...core.resources import resource_tracker
from ...core.workers import set_worker_controller
from .component_bootstrap import initialize_components
from .edge_service_runtime import start_edge_services
from .hot_reload_runtime import start_hot_reload_manager
from .logging_bootstrap import (
    get_gateway_logger,
    initialize_lifecycle_loggers,
    log_startup_exception,
    setup_logging_from_config,
)
from .model_validation_startup import validate_gateway_models
from .shutdown_sequence import run_shutdown_sequence
from .worker_runtime_startup import start_worker_runtime


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:  # noqa: PLR0912, PLR0915
    """Application lifespan management (startup orchestration + yield + shutdown).

    This is the only public API symbol of the `app.lifecycle` package. It
    performs a strict, ordered startup of all subsystems, yields control to
    the FastAPI application for its entire lifetime, and guarantees that
    `run_shutdown_sequence` is executed on exit even if startup partially
    succeeded.

    The implementation is intentionally a high-level coordinator; every
    substantive concern has been delegated to a dedicated module inside this
    package so that the overall flow remains readable and each phase is
    independently testable.
    """
    try:
        config_loader = get_config_loader()

        # CRITICAL: Setup logging FIRST before any logging calls
        # This prevents universal_logging auto-initialization
        # from overriding YAML config
        setup_logging_from_config(config_loader)

        # Initialize loggers AFTER logging configuration is applied
        initialize_lifecycle_loggers()

        gateway_logger = get_gateway_logger()

        # Startup logging (now with proper configuration applied)
        if gateway_logger is not None:
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
        validation_report = validate_gateway_models(model_registry)

        # Start worker controller, VRAM reconciler, orphan cleanup, crash handlers
        await start_worker_runtime(app, event_bus, worker_controller)

        # Startup completed log (moved here from the old worker block)
        valid_count = validation_report.valid_models if validation_report else "skipped"
        if gateway_logger is not None:
            gateway_logger.info(
                f"Universal LLM Gateway startup completed: "
                f"version={__version__}, "
                f"models_available={len(model_registry.get_available_synthetic_model_ids())}, "
                f"models_valid={valid_count}, "
                f"phase=2-Process_Isolation_Architecture"
            )

        # Initialize hot reload manager if enabled
        await start_hot_reload_manager(app, gateway_config, model_registry, event_bus)

        # Event-driven crash detection - no health monitoring needed
        # process_ipc handles internal health monitoring and publishes events
        if gateway_logger is not None:
            gateway_logger.info(
                "Event-driven crash detection enabled (no health monitoring)"
            )

        # Initialize and start peripheral edge services
        await start_edge_services(app)

    except Exception as e:
        # Log the error with full stack trace using the canonical helper
        # (replaces the original stdlib logging fallback).
        log_startup_exception(e)
        # Re-raise to preserve the original stack trace.
        raise

    # DIAGNOSTIC: Log before yield
    gateway_logger = get_gateway_logger()
    if gateway_logger is not None:
        gateway_logger.error(
            "🚨 GATEWAY LIFESPAN: About to yield (entering application run phase)"
        )

    yield

    # DIAGNOSTIC: Log after yield (this means shutdown was initiated)
    gateway_logger = get_gateway_logger()
    if gateway_logger is not None:
        gateway_logger.error("🚨 GATEWAY LIFESPAN: Exited yield - shutdown initiated!")
        gateway_logger.error(
            f"🚨 Call stack at yield exit:\n{''.join(traceback.format_stack())}"
        )

    # Shutdown - always attempt a clean sequence
    await run_shutdown_sequence(app)
