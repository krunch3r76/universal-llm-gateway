"""Failed worker cleanup with guaranteed process termination after load failures."""

from typing import TYPE_CHECKING

from .deps import (
    emit_load_flow_debug,
    get_resource_tracker,
    is_cleanup_in_progress,
    logger,
    mark_cleanup_finished,
    mark_cleanup_started,
)

if TYPE_CHECKING:
    from ...controller import WorkerController


def is_model_cleanup_in_progress(model_id: str) -> bool:
    """True while cleanup_failed_worker is running for this model_id."""
    return is_cleanup_in_progress(model_id)


async def cleanup_failed_worker(
    controller: "WorkerController", model_id: str, reason: str
):
    """
    Clean up after worker failure with guaranteed process termination.

    Flow:
    1. Try supervisor.stop() (graceful)
    2. Verify process is dead
    3. If still alive, force-kill by PID
    4. Only then clean up state

    This is the EVENT HANDLER for load failures - must be robust.
    """
    mark_cleanup_started(model_id)
    await emit_load_flow_debug("cleanup_failed_worker_start", model_id, reason=reason)
    try:
        await _cleanup_failed_worker_inner(controller, model_id, reason)
    finally:
        mark_cleanup_finished(model_id)
        await emit_load_flow_debug(
            "cleanup_failed_worker_done", model_id, reason=reason
        )


async def _cleanup_failed_worker_inner(
    controller: "WorkerController", model_id: str, reason: str
):
    resource_tracker = get_resource_tracker()
    supervisor = controller._process_state.get_supervisor(model_id)

    process_killed = False
    pid = None

    if supervisor:
        # Get PID before we lose the supervisor reference
        try:
            proc_info = controller.get_all_process_info().get(model_id, {})
            pid = proc_info.get("pid") if isinstance(proc_info, dict) else None
        except Exception as e:
            logger.debug(
                "Failed to get process info for %s during cleanup: %s", model_id, e
            )

        # Try graceful stop first
        try:
            await supervisor.stop(force=True, timeout=3)
            logger.debug("Supervisor stop attempted for %s", model_id)
            await emit_load_flow_debug(
                "cleanup_supervisor_stop_attempted", model_id, pid=pid
            )
        except Exception as e:
            logger.warning("Supervisor stop failed for %s: %s", model_id, e)
            await emit_load_flow_debug(
                "cleanup_supervisor_stop_exception",
                model_id,
                pid=pid,
                error_type=type(e).__name__,
                error=str(e),
            )

        # Always verify process is actually dead, regardless of supervisor.stop outcome
        if pid:
            try:
                import psutil

                if psutil.pid_exists(pid):
                    logger.warning(
                        "Process %s for %s still alive after stop, force killing",
                        pid,
                        model_id,
                    )
                    try:
                        await emit_load_flow_debug(
                            "cleanup_force_kill_start", model_id, pid=pid
                        )
                        await controller._lifecycle_manager.kill_pid_tree(pid, model_id)
                        # Re-check if process is dead after force kill
                        process_killed = not psutil.pid_exists(pid)
                        await emit_load_flow_debug(
                            "cleanup_force_kill_done",
                            model_id,
                            pid=pid,
                            process_killed=process_killed,
                        )
                    except Exception as kill_e:
                        logger.error(
                            "Failed to force kill process %s for %s: %s",
                            pid,
                            model_id,
                            kill_e,
                        )
                        process_killed = False  # Force kill failed
                else:
                    process_killed = True  # Process was already dead
                    await emit_load_flow_debug(
                        "cleanup_pid_already_dead", model_id, pid=pid
                    )
            except Exception as e:
                logger.error(
                    "Process existence check or force kill failed for %s (PID %s): %s",
                    model_id,
                    pid,
                    e,
                )
                await emit_load_flow_debug(
                    "cleanup_pid_check_exception",
                    model_id,
                    pid=pid,
                    error_type=type(e).__name__,
                    error=str(e),
                )
        else:
            process_killed = True
            await emit_load_flow_debug("cleanup_no_pid", model_id)

        # Clean up state only after process is confirmed dead
        controller._process_state.remove_supervisor(model_id)
        controller._process_state.remove_socket_path(model_id)
        controller._process_state.remove_engine_pid(model_id)

    # Clean up socket file (may exist even if supervisor doesn't)
    await controller._cleanup_socket_file(model_id)

    # Update tracker state (process dead or no supervisor — reconcile SM + status)
    resource_tracker.set_model_not_loaded(model_id, reason)
    await emit_load_flow_debug(
        "cleanup_tracker_set_not_loaded",
        model_id,
        pid=pid,
        process_killed=process_killed,
        reason=reason,
    )

    if process_killed or not pid:
        logger.info(f"✅ Cleaned up failed worker: {model_id} (reason: {reason})")
    else:
        logger.error(
            f"❌ Failed to clean up worker {model_id} - process may still be running"
        )
