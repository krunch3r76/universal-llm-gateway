"""Worker-runtime startup, orphan cleanup, and crash handler installation.

Extracted from the monolithic lifespan so that worker process lifecycle,
VRAM reconciliation, orphaned socket recovery, and event-driven crash
handling are isolated and testable.
"""

from pathlib import Path

from ...core.events.types import ModelUnloaded
from ...core.resources import resource_tracker
from ...core.resources.vram_reconciler import VramReconciler
from ...core.workers.orphan_detector import OrphanedSocketDetector
from ...core.workers.process_crash_bridge import ProcessCrashBridge
from ...core.workers.resource_tracker_crash_handler import ResourceTrackerCrashHandler
from ...core.workers.stream_cancellation_handler import StreamCancellationHandler
from .logging_bootstrap import get_gateway_logger


async def start_worker_runtime(app, event_bus, worker_controller) -> None:
    """Start the worker controller, VRAM reconciler, perform orphan cleanup,
    and install the minimal event-driven crash handlers.

    This is the single entry point called by the lifespan orchestrator after
    components have been created and model validation has completed. It
    mutates app.state to expose vram_reconciler and the three crash handlers
    for later shutdown and diagnostics.
    """
    # Start worker controller
    await worker_controller.start()

    vram_reconciler = VramReconciler(
        resource_tracker=resource_tracker,
        worker_controller=worker_controller,
        event_bus=event_bus,
    )
    app.state.vram_reconciler = vram_reconciler
    await vram_reconciler.start()

    # Clean up orphaned workers from previous gateway instance
    await cleanup_orphaned_workers(event_bus, worker_controller)

    # Initialize minimal event-driven crash handling
    install_worker_event_handlers(app, event_bus, worker_controller)


async def cleanup_orphaned_workers(event_bus, worker_controller) -> None:
    """Detect and clean up sockets left by workers from a previous gateway
    instance (e.g., after a crash or forced restart).

    This keeps the Stargate coordinator's view of loaded models in sync with
    reality. Any detected orphans cause a ModelUnloaded event to be published
    and a fallback process cleanup to be invoked.
    """
    gateway_logger = get_gateway_logger()

    try:
        orphan_detector = OrphanedSocketDetector(worker_controller)
        socket_dir = Path("/tmp/universal-protocol")
        orphaned = await orphan_detector.find_orphaned_sockets(socket_dir)

        if orphaned:
            if gateway_logger is not None:
                gateway_logger.info(
                    f"🧹 Found {len(orphaned)} orphaned worker(s) from "
                    f"previous gateway instance, cleaning up..."
                )
            for model_id, socket_path, reason in orphaned:
                if gateway_logger is not None:
                    gateway_logger.info(
                        f"🧹 Cleaning up orphaned worker: {model_id} (reason: {reason})"
                    )
                try:
                    # Broadcast MODEL_UNLOADED to notify Stargate
                    # (converted from original relative import inside the block)
                    await event_bus.publish_nowait(ModelUnloaded(model_id=model_id))

                    # Kill orphaned process and clean up socket
                    cleanup_fn = worker_controller._cleanup_socket_file
                    await worker_controller._lifecycle_manager.fallback_process_cleanup(  # noqa: E501
                        model_id, cleanup_fn
                    )
                    if gateway_logger is not None:
                        gateway_logger.info(
                            f"✅ Cleaned up orphaned worker: {model_id}"
                        )
                except Exception as cleanup_err:
                    if gateway_logger is not None:
                        gateway_logger.warning(
                            f"Failed to clean up orphaned worker "
                            f"{model_id}: {cleanup_err}"
                        )
        else:
            if gateway_logger is not None:
                gateway_logger.debug("No orphaned workers detected on startup")
    except Exception as e:
        if gateway_logger is not None:
            gateway_logger.warning(f"Orphan detection failed on startup: {e}")


def install_worker_event_handlers(app, event_bus, worker_controller) -> None:
    """Create the three minimal event-driven crash/stream handlers and store
    them on app.state for visibility and shutdown.

    The original absolute imports have been converted to package-relative
    imports using the `...` form required at src/app/lifecycle/ depth:

        from ...core.workers.process_crash_bridge import ProcessCrashBridge
        ...
    """
    # Bridge process_ipc crash events to gateway crash events
    # (converted from `from src.core...`)
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
