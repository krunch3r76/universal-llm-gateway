"""Model loading flow operations: worker lifecycle, verification, finalization."""

from typing import TYPE_CHECKING, Any

from process_ipc import ProcessStatus
from universal_logging import get_logger

from ..state_machine import WorkerState

if TYPE_CHECKING:
    from ..controller import WorkerController


def _get_resource_tracker():
    from src.core.resources import resource_tracker

    return resource_tracker


def _get_event_classes():
    from src.core.events.types import ModelLoaded, ModelLoadFailed, ModelLoadingStarted

    return ModelLoadFailed, ModelLoaded, ModelLoadingStarted


async def _publish_event(event_bus, event) -> bool:
    """Publish event with error handling. Returns True if published."""
    if not event_bus:
        return False
    try:
        await event_bus.publish_async_nowait(event)
        return True
    except Exception as e:
        get_logger(__name__).warning(f"⚠️ Failed to publish event: {e}")
        return False


logger = get_logger(__name__)
structured_logger = get_logger("universal_llm_gateway.load_flow")


async def emit_loading_event(
    controller: "WorkerController", model_id: str, status: str, error: str = None
):
    """Emit model loading events."""
    model_load_failed, model_loaded, model_loading_started = _get_event_classes()
    if status == "started":
        await _publish_event(
            controller.event_bus,
            model_loading_started(model_id=model_id),
        )
    elif status == "failed":
        await _publish_event(
            controller.event_bus,
            model_load_failed(model_id=model_id, error_message=error or "Unknown"),
        )


def reset_state_machine(model_id: str):
    """Reset state machine for fresh load."""
    try:
        sm = _get_resource_tracker().get_state_machine(model_id)
        if sm and sm.current_state != WorkerState.LOADING:
            sm.transition(
                WorkerState.LOADING,
                "Model loading initiated",
                metadata={"reset_before_load": True},
            )
    except Exception as e:
        logger.error(
            "Critical error resetting state machine for %s: %s", model_id, e
        )


async def measure_vram_before(model_id: str) -> float | None:
    """Measure VRAM before loading for delta calculation."""
    try:
        return (await _get_resource_tracker().get_system_resources()).available_vram_mb
    except Exception as e:
        logger.warning("Failed to measure VRAM before loading: %s", e)
        return None


async def start_worker_if_needed(controller: "WorkerController", model_id: str) -> bool:
    """Start worker process if not running or orphaned."""
    procs = controller.get_all_process_info()
    resource_tracker = _get_resource_tracker()

    # Check if process exists and is running
    process_info = procs.get(model_id) if model_id in procs else None
    if process_info and process_info.get("status") == ProcessStatus.RUNNING.value:
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
                if alive:
                    # Process is alive but not tracked - reconcile it
                    logger.info(
                        f"✅ Reconciling orphaned worker {model_id} into "
                        f"resource tracker"
                    )
                    resource_tracker.register_model(model_id)
                    # Don't set as loaded yet - let the normal flow verify
                    # and configure it
                    return True
            except Exception as e:
                logger.warning(f"Orphaned process health check failed: {e}")

            # Process is dead or unresponsive - clean it up and start fresh
            logger.info(f"🧹 Cleaning up unresponsive orphaned process for {model_id}")
            await controller._lifecycle_manager.cleanup_stale_process(model_id)
            # Fall through to start new worker
        else:
            # Process exists and is tracked - reuse it
            return True

    # No process or process was cleaned up - start new worker
    if not await start_worker(controller, model_id):
        logger.error(f"❌ Failed to start worker for {model_id}")
        resource_tracker.set_model_error(model_id, "Failed to start worker")
        await cleanup_failed_worker(controller, model_id, "Worker start failed")
        return False
    return True


async def start_worker(controller: "WorkerController", model_id: str) -> bool:
    """Start a new worker process."""

    async def verify(mid):
        return await controller._lifecycle_manager.verify_process_alive(
            mid, controller.get_all_process_info
        )

    async def cleanup(mid):
        await controller._lifecycle_manager.cleanup_stale_process(mid)

    async def diag(mid, cmd, env):
        logger.error(f"❌ Worker startup failure for {mid}: {' '.join(cmd)}")

    return await controller._lifecycle_manager.start_worker(
        model_id, controller._create_transport_config, verify, diag
    )


async def send_model_config(
    controller: "WorkerController", model_id: str
) -> dict[str, Any] | None:
    """
    Send model configuration to worker.

    Cleanup is handled via event-driven system - no callbacks needed.

    Returns:
        dict with keys: {'success': bool, 'context_size': int} on success
        None on failure
    """
    from ...errors import WorkerInitializationError

    supervisor = controller._process_state.get_supervisor(model_id)
    if not supervisor:
        raise WorkerInitializationError(
            message=f"No supervisor for {model_id}",
            internal_error="Supervisor missing",
            context={"model_id": model_id},
        )

    return await controller._communication_manager.send_model_config(
        model_id,
        supervisor,
    )


async def verify_model_responsive(
    controller: "WorkerController", model_id: str
) -> bool:
    """Verify model is responsive after loading."""
    if not await controller.is_process_alive(model_id):
        logger.error(f"❌ Model {model_id} not responsive")
        _get_resource_tracker().set_model_error(model_id, "Model not responsive")
        await cleanup_failed_worker(controller, model_id, "Unresponsive process")
        return False
    return True


async def finalize_load(
    controller: "WorkerController",
    model_id: str,
    vram_before: float | None,
    context_length: int | None = None,
):
    """Finalize model loading with resource measurement."""
    resource_tracker = _get_resource_tracker()
    pid = None
    try:
        info = controller.get_all_process_info().get(model_id)
        if info and isinstance(info, dict):
            pid = info.get("pid")
    except Exception as e:
        logger.debug(
            "Could not get process info for %s during finalization: %s",
            model_id,
            e,
        )

    req = resource_tracker.get_model_requirements(model_id)
    actual_vram, actual_ram = (
        req["vram_required_mb"] or 0,
        req["ram_required_mb"] or 0,
    )
    if pid:
        v, r = resource_tracker.get_current_process_resources(
            pid=pid, model_id=model_id
        )
        if v is not None:
            actual_vram = v
        if r is not None:
            actual_ram = r

    resource_tracker.update_model_resources(model_id, actual_vram, actual_ram)
    _, model_loaded, _ = _get_event_classes()

    await _publish_event(
        controller.event_bus,
        model_loaded(
            model_id=model_id,
            vram_usage_mb=actual_vram,
            ram_usage_mb=actual_ram,
            process_pid=pid,
        ),
    )

    # Verify tracker state before publishing RESOURCE_UPDATE
    tracker_info = resource_tracker.get_model_info(model_id)
    if tracker_info:
        logger.info(
            f"🔍 PRE-RESOURCE_UPDATE: {model_id} tracker state: "
            f"status={tracker_info.status.value}, "
            f"vram={tracker_info.vram_usage_mb}MB, "
            f"ram={tracker_info.ram_usage_mb}MB"
        )
    else:
        logger.error(f"❌ PRE-RESOURCE_UPDATE: {model_id} NOT IN TRACKER!")

    # Publish RESOURCE_UPDATE with correct available VRAM (model now LOADED)
    # This ensures Stargate's cache reflects the loaded model's VRAM usage.
    # Without this, earlier RESOURCE_UPDATEs (from preflight/measure_vram_before)
    # show stale values because model was LOADING, not LOADED.
    _ = await resource_tracker.get_system_resources()

    logger.info(
        f"✅ Model {model_id} loaded - VRAM: {actual_vram}MB, RAM: {actual_ram}MB"
        + (f", Context: {context_length}" if context_length else "")
    )


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
    resource_tracker = _get_resource_tracker()
    supervisor = controller._process_state.get_supervisor(model_id)

    process_killed = False
    pid = None

    if supervisor:
        # Get PID before we lose the supervisor reference
        try:
            proc_info = controller.get_all_process_info().get(model_id, {})
            pid = proc_info.get("pid") if isinstance(proc_info, dict) else None
        except Exception:
            pass

        # Try graceful stop first
        try:
            await supervisor.stop(force=True, timeout=3)
            logger.debug("Supervisor stop attempted for %s", model_id)
        except Exception as e:
            logger.warning("Supervisor stop failed for %s: %s", model_id, e)

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
                    await controller._lifecycle_manager.kill_pid_tree(pid, model_id)
                    process_killed = True
                else:
                    process_killed = True
            except Exception as e:
                logger.error(
                    "Process existence check or force kill failed for %s (PID %s): %s",
                    model_id,
                    pid,
                    e,
                )
        else:
            process_killed = True

        # Clean up state only after process is confirmed dead
        controller._process_state.remove_supervisor(model_id)
        controller._process_state.remove_socket_path(model_id)
        controller._process_state.remove_engine_pid(model_id)

    # Clean up socket file (may exist even if supervisor doesn't)
    await controller._cleanup_socket_file(model_id)

    # Update tracker state
    sm = resource_tracker.get_state_machine(model_id)
    if sm and sm.clear_error(reason):
        from src.core.resources import ModelStatus

        resource_tracker.set_model_status(model_id, ModelStatus.NOT_LOADED)

    if process_killed or not pid:
        logger.info(f"✅ Cleaned up failed worker: {model_id} (reason: {reason})")
    else:
        logger.error(
            f"❌ Failed to clean up worker {model_id} - process may still be running"
        )


async def handle_load_exception(
    controller: "WorkerController", model_id: str, e: Exception
):
    """Handle exception during model loading with error classification."""
    error_str = str(e).lower()

    # Classify error type for client visibility and telemetry
    if _is_oom_error(error_str):
        error_msg = f"OOM:{str(e)}"  # Prefix for detection by Stargate
        failure_reason = "oom"
        logger.error(f"❌ OOM error loading {model_id}: {e}")
    elif _is_resource_error(error_str):
        error_msg = f"RESOURCE:{str(e)}"
        failure_reason = "insufficient_resources"
        logger.error(f"❌ Resource error loading {model_id}: {e}")
    elif "timeout" in error_str:
        error_msg = str(e)
        failure_reason = "timeout"
        logger.error(f"❌ Timeout loading {model_id}: {e}")
    elif "not found" in error_str or "no such file" in error_str:
        error_msg = str(e)
        failure_reason = "missing_file"
        logger.error(f"❌ File not found loading {model_id}: {e}")
    elif "config" in error_str or "invalid" in error_str:
        error_msg = str(e)
        failure_reason = "config_error"
        logger.error(f"❌ Configuration error loading {model_id}: {e}")
    else:
        error_msg = str(e)
        failure_reason = "unknown"
        logger.error(f"❌ Error loading {model_id}: {e}")

    _get_resource_tracker().set_model_error(model_id, error_msg)

    # Emit event with failure reason for observability (Recommendation #7)
    from src.core.events.types import ModelLoadFailed

    if controller.event_bus:
        await controller.event_bus.publish_async_nowait(
            ModelLoadFailed(
                model_id=model_id,
                error_message=error_msg,
                failure_reason=failure_reason,
            )
        )

    # Also emit for Stargate WebSocket notification (backward compat)
    await emit_loading_event(controller, model_id, "failed", error_msg)
    await cleanup_failed_worker(controller, model_id, "Load exception")


def _is_oom_error(error_str: str) -> bool:
    """Check if error is an OOM (Out of Memory) error."""
    oom_indicators = [
        "out of memory",
        "cuda out of memory",
        "oom",
        "cuda oom",
        "memory error",
        "allocation failed",
        "cannot allocate",
    ]
    return any(indicator in error_str for indicator in oom_indicators)


def _is_resource_error(error_str: str) -> bool:
    """Check if error is a resource constraint error."""
    resource_indicators = [
        "insufficient",
        "not enough",
        "exceeded",
        "quota",
    ]
    return any(indicator in error_str for indicator in resource_indicators)
