"""Gateway shutdown sequence and drain signaling.

Owns the graceful_shutdown logic (event-driven idle wait with GATEWAY_DRAINING),
the full ordered run_shutdown_sequence, VRAM reconciler stop, GatewayShutdown
event emission, debug broadcaster stop, worker/hot stops, and edge service
shutdown delegation. All shutdown paths are contained here so the FastAPI
lifespan can simply `await run_shutdown_sequence(app)` after yield.
"""

import asyncio
import os
import socket
import time

from fastapi import FastAPI

from ...core.events.types import GatewayDraining, GatewayShutdown
from .edge_service_runtime import stop_edge_services
from .logging_bootstrap import get_gateway_logger


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
    gateway_logger = get_gateway_logger()

    gateway_name = os.environ.get("GATEWAY_NAME", socket.gethostname())

    # Emit GATEWAY_DRAINING event
    try:
        if hasattr(app.state, "event_bus") and app.state.event_bus:
            drain_event = GatewayDraining(
                gateway_id=gateway_name,
                reason="graceful_shutdown",
                timeout=timeout,
                timestamp=time.time(),
            )
            await app.state.event_bus.publish_nowait(drain_event)
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
    def on_idle_callback() -> None:
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


async def run_shutdown_sequence(app: FastAPI) -> None:
    """Execute the complete, ordered gateway shutdown sequence.

    The sequence is:
    1. Log start of shutdown
    2. Stop VRAM reconciler (best effort)
    3. If GATEWAY_SHUTDOWN_MODE=graceful, run graceful drain logic
    4. Fire-and-forget a GATEWAY_SHUTDOWN event
    5. Stop edge services (rate limiter, metrics, WS forwarder)
    6. Stop debug broadcaster (must precede EventBus teardown)
    7. Stop worker controller (terminates all model worker processes)
    8. Stop hot reload manager
    9. Log completion

    All steps are wrapped so that a failure in one phase does not prevent
    subsequent cleanup. The original monolithic shutdown block has been
    fully moved here.
    """
    gateway_logger = get_gateway_logger()

    try:
        if gateway_logger is not None:
            gateway_logger.info("Universal LLM Gateway shutting down")

        # Check shutdown mode
        shutdown_mode = os.environ.get("GATEWAY_SHUTDOWN_MODE", "fast")

        if hasattr(app.state, "vram_reconciler") and app.state.vram_reconciler:
            try:
                await app.state.vram_reconciler.stop()
            except Exception as e:
                if gateway_logger is not None:
                    gateway_logger.warning(f"Error stopping VRAM reconciler: {e}")

        if shutdown_mode == "graceful":
            graceful_timeout = float(os.environ.get("GATEWAY_GRACEFUL_TIMEOUT", "30"))
            if gateway_logger is not None:
                gateway_logger.info(
                    f"Graceful shutdown mode (timeout={graceful_timeout}s)"
                )
            await graceful_shutdown(app, timeout=graceful_timeout)

        # Emit GATEWAY_SHUTDOWN event (always, after graceful if applicable)
        try:
            gateway_name = os.environ.get("GATEWAY_NAME", socket.gethostname())
            if hasattr(app.state, "event_bus") and app.state.event_bus:
                shutdown_event = GatewayShutdown(
                    gateway_id=gateway_name,
                    reason="shutdown",
                    timestamp=time.time(),
                )
                # True fire-and-forget: schedule but don't await
                asyncio.create_task(app.state.event_bus.publish_nowait(shutdown_event))
                if gateway_logger is not None:
                    gateway_logger.info(
                        f"Emitted GATEWAY_SHUTDOWN event for {gateway_name}"
                    )
        except Exception as e:
            if gateway_logger is not None:
                gateway_logger.warning(f"Failed to emit shutdown event: {e}")

        # Stop edge services (rate limiter, state metrics, WS forwarder)
        # This replaces the inline shutdown blocks that used to live in lifespan.
        try:
            await stop_edge_services(app)
        except Exception as e:
            if gateway_logger is not None:
                gateway_logger.warning(f"Error stopping edge services: {e}")

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
