"""Worker process startup and orphan reconciliation during model load."""

from typing import TYPE_CHECKING

from process_ipc import ProcessStatus

from .cleanup import cleanup_failed_worker
from .deps import emit_load_flow_debug, get_resource_tracker, logger
from .events import emit_loading_event
from .failure_classify import resolve_load_failure

if TYPE_CHECKING:
    from ...controller import WorkerController


async def start_worker(controller: "WorkerController", model_id: str) -> bool:
    """Start a new worker process."""

    async def verify(mid):
        return await controller._lifecycle_manager.verify_process_alive(
            mid, controller.get_all_process_info
        )

    async def cleanup(mid):
        await controller._lifecycle_manager.cleanup_stale_process(mid)

    async def diag(mid, cmd, env):
        error_msg = f"❌ Worker startup failure for {mid}: {' '.join(cmd)}"
        logger.error(error_msg)
        raise RuntimeError(
            error_msg
        )  # Propagate the error to ensure start_worker fails

    return await controller._lifecycle_manager.start_worker(
        model_id, controller._create_transport_config, verify, diag
    )


async def start_worker_if_needed(controller: "WorkerController", model_id: str) -> bool:
    """Start worker process if not running or orphaned."""
    procs = controller.get_all_process_info()
    resource_tracker = get_resource_tracker()

    # Check if process exists and is running
    process_info = procs.get(model_id) if model_id in procs else None
    if process_info and process_info.get("status") == ProcessStatus.RUNNING.value:
        await emit_load_flow_debug(
            "worker_running_seen",
            model_id,
            pid=process_info.get("pid"),
            tracked=model_id in resource_tracker.get_loaded_models(),
        )
        # Process exists - verify it's actually tracked and healthy
        tracked_models = resource_tracker.get_loaded_models()
        if model_id not in tracked_models:
            # Orphaned process - not tracked as loaded
            logger.warning(
                f"⚠️ Orphaned worker process detected for {model_id}, "
                f"attempting to reconcile or restart"
            )
            # Try to verify if process is actually responsive
            try:
                # Quick health check - if this succeeds, reconcile it
                alive = await controller._lifecycle_manager.verify_process_alive(
                    model_id, controller.get_all_process_info
                )
                await emit_load_flow_debug(
                    "orphan_health_checked", model_id, alive=alive
                )
                if alive:
                    # Process is alive but not tracked - reconcile it
                    logger.info(
                        f"✅ Reconciling orphaned worker {model_id} into "
                        f"resource tracker. Continuing with verification and configuration."
                    )
                    resource_tracker.register_model(model_id)
                    # Allow the normal flow to verify and configure it.
                    # Do not return here; let the function proceed to send_model_config and verify_model_responsive.
                    # The existing process will be reused if it passes subsequent checks.
                    await emit_load_flow_debug("orphan_reconciled", model_id)
                    return True  # This return needs to be removed or logic adjusted to ensure full load flow.
            except Exception as e:
                logger.warning(f"Orphaned process health check failed: {e}")
                await emit_load_flow_debug(
                    "orphan_health_exception",
                    model_id,
                    error_type=type(e).__name__,
                    error=str(e),
                )

            # Process is dead or unresponsive - clean it up and start fresh
            logger.info(f"🧹 Cleaning up unresponsive orphaned process for {model_id}")
            await emit_load_flow_debug("orphan_cleanup_start", model_id)
            await controller._lifecycle_manager.cleanup_stale_process(model_id)
            await emit_load_flow_debug("orphan_cleanup_done", model_id)
            # Fall through to start new worker
        else:
            # Process exists and is tracked - reuse it
            await emit_load_flow_debug("worker_reused", model_id)
            return True

    # No process or process was cleaned up - start new worker
    await emit_load_flow_debug("worker_start_needed", model_id)
    started = await start_worker(controller, model_id)
    await emit_load_flow_debug("worker_start_done", model_id, started=started)
    if not started:
        error_msg, _ = resolve_load_failure(model_id, "Failed to start worker")
        logger.error("❌ Failed to start worker for %s: %s", model_id, error_msg)
        resource_tracker.set_model_error(model_id, error_msg)
        await emit_loading_event(controller, model_id, "failed", error_msg)
        await cleanup_failed_worker(controller, model_id, "Worker start failed")
        return False
    return True
