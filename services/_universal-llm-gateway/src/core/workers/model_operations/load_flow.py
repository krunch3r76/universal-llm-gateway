"""Model loading flow operations: worker lifecycle, verification, finalization."""

import asyncio
from typing import TYPE_CHECKING, Any

from process_ipc import ProcessStatus
from universal_event_bus.events.debug import emit_debug_event
from universal_logging import get_logger

from ..state_machine import WorkerState

if TYPE_CHECKING:
    from ..controller import WorkerController


def _get_resource_tracker():
    """Lazily imports and returns the global resource_tracker instance."""
    from src.core.resources import resource_tracker

    return resource_tracker


def _get_event_classes():
    """Lazily imports and returns the event classes for model loading."""
    from src.core.events.types import ModelLoaded, ModelLoadFailed, ModelLoadingStarted

    return ModelLoadFailed, ModelLoaded, ModelLoadingStarted


async def _publish_event(event_bus, event) -> bool:
    """Publish event with error handling. Returns True if published."""
    if not event_bus:
        return False
    try:
        await event_bus.publish_nowait(event)
        return True
    except Exception as e:
        get_logger(__name__).warning(f"⚠️ Failed to publish event: {e}")
        return False


logger = get_logger(__name__)
structured_logger = get_logger("universal_llm_gateway.load_flow")

# Models with an in-flight failed-load cleanup; blocks concurrent loads until done.
_cleanup_in_progress: set[str] = set()


async def _emit_load_flow_debug(step: str, model_id: str, **extra: Any) -> None:
    await emit_debug_event(
        "debug.load.flow",
        {
            "step": step,
            "model_id": model_id,
            **extra,
        },
        source="gateway",
    )


def _classify_load_failure(error_message: str) -> tuple[str, str]:
    """Normalize failure text and derive a stable reason code."""
    normalized = (error_message or "Unknown error").strip()
    lower = normalized.lower()

    if normalized.startswith("OOM:"):
        return normalized, "oom"
    if normalized.startswith("RESOURCE:"):
        return normalized, "insufficient_resources"
    if _is_oom_error(lower):
        return f"OOM:{normalized}", "oom"
    if _is_resource_error(lower):
        return f"RESOURCE:{normalized}", "insufficient_resources"
    if "timeout" in lower or "timed out" in lower:
        return normalized, "timeout"
    if "not found" in lower or "no such file" in lower:
        return normalized, "missing_file"
    if "config" in lower or "invalid" in lower:
        return normalized, "config_error"
    return normalized, "unknown"


def _resolve_load_failure(model_id: str, fallback: str) -> tuple[str, str]:
    """Prefer any recorded tracker error before falling back to a generic failure."""
    model_info = _get_resource_tracker().get_model_info(model_id)
    existing_error = (
        model_info.error_message
        if model_info is not None and model_info.error_message
        else fallback
    )
    return _classify_load_failure(existing_error)


def is_model_cleanup_in_progress(model_id: str) -> bool:
    """True while cleanup_failed_worker is running for this model_id."""
    return model_id in _cleanup_in_progress


async def emit_loading_event(
    controller: "WorkerController",
    model_id: str,
    status: str,
    error: str | None = None,
) -> None:
    """Emit model loading lifecycle events (started, failed).

    Publishes MODEL_LOADING_STARTED or MODEL_LOAD_FAILED via the controller's
    event bus. Does not handle the "loaded" status - MODEL_LOADED is emitted
    by finalize_load() after resource measurement.

    On status="failed": attaches a best-effort worker_snapshot capturing
    peer worker processes, llama-cpp/vLLM child processes, and live
    hardware VRAM/RAM at failure time. Forensics-only — snapshot capture
    failures degrade silently and never block event emission.
    """
    model_load_failed, _, model_loading_started = _get_event_classes()
    event_to_publish = None
    if status == "started":
        event_to_publish = model_loading_started(model_id=model_id)
    elif status == "failed":
        classified_error, failure_reason = _classify_load_failure(error or "Unknown")
        from .failure_snapshot import build_worker_snapshot

        worker_snapshot = build_worker_snapshot(controller, model_id)
        event_to_publish = model_load_failed(
            model_id=model_id,
            error_message=classified_error,
            failure_reason=failure_reason,
            worker_snapshot=worker_snapshot,
        )
    if event_to_publish:
        await _publish_event(controller.event_bus, event_to_publish)


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
        logger.error("Failed to reset state machine for %s: %s", model_id, e)
        # Depending on criticality, consider re-raising or more specific handling
        # raise # Example: if this error should halt the load flow


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
        await _emit_load_flow_debug(
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
                await _emit_load_flow_debug(
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
                    await _emit_load_flow_debug("orphan_reconciled", model_id)
                    return True  # This return needs to be removed or logic adjusted to ensure full load flow.
            except Exception as e:
                logger.warning(f"Orphaned process health check failed: {e}")
                await _emit_load_flow_debug(
                    "orphan_health_exception",
                    model_id,
                    error_type=type(e).__name__,
                    error=str(e),
                )

            # Process is dead or unresponsive - clean it up and start fresh
            logger.info(f"🧹 Cleaning up unresponsive orphaned process for {model_id}")
            await _emit_load_flow_debug("orphan_cleanup_start", model_id)
            await controller._lifecycle_manager.cleanup_stale_process(model_id)
            await _emit_load_flow_debug("orphan_cleanup_done", model_id)
            # Fall through to start new worker
        else:
            # Process exists and is tracked - reuse it
            await _emit_load_flow_debug("worker_reused", model_id)
            return True

    # No process or process was cleaned up - start new worker
    await _emit_load_flow_debug("worker_start_needed", model_id)
    started = await start_worker(controller, model_id)
    await _emit_load_flow_debug("worker_start_done", model_id, started=started)
    if not started:
        error_msg, _ = _resolve_load_failure(model_id, "Failed to start worker")
        logger.error("❌ Failed to start worker for %s: %s", model_id, error_msg)
        resource_tracker.set_model_error(model_id, error_msg)
        await emit_loading_event(controller, model_id, "failed", error_msg)
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
        error_msg = f"❌ Worker startup failure for {mid}: {' '.join(cmd)}"
        logger.error(error_msg)
        raise RuntimeError(
            error_msg
        )  # Propagate the error to ensure start_worker fails

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

    supervisor = controller._process_state.get_supervisor(model_id)
    if not supervisor:
        error_msg, _ = _resolve_load_failure(
            model_id, "No supervisor available during model config send"
        )
        logger.error(
            "No supervisor for %s during model config send: %s", model_id, error_msg
        )
        _get_resource_tracker().set_model_error(model_id, error_msg)
        await emit_loading_event(controller, model_id, "failed", error_msg)
        await cleanup_failed_worker(controller, model_id, "Model config send failed")
        return None

    return await controller._communication_manager.send_model_config(
        model_id,
        supervisor,
    )


async def verify_model_responsive(
    controller: "WorkerController", model_id: str
) -> bool:
    """Verify model is responsive after loading."""
    alive = await controller.is_process_alive(model_id)
    await _emit_load_flow_debug("verify_process_alive", model_id, alive=alive)
    if not alive:
        error_msg, _ = _resolve_load_failure(model_id, "Model not responsive")
        logger.error("❌ Model %s not responsive: %s", model_id, error_msg)
        _get_resource_tracker().set_model_error(model_id, error_msg)
        await emit_loading_event(controller, model_id, "failed", error_msg)
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
    except KeyError:  # Example: if get() might raise KeyError if model_id not found
        logger.debug(
            "Process info not found for %s during finalization.",
            model_id,
        )
    except Exception as e:  # Catch other unexpected errors
        logger.warning(
            "Unexpected error getting process info for %s during finalization: %s",
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
    reconciled_vram = await asyncio.to_thread(
        _reconcile_catalog_vram,
        model_id,
        actual_vram,
        actual_ram,
    )
    if reconciled_vram:
        from src.core.events.types import CatalogReloaded

        await _publish_event(
            controller.event_bus,
            CatalogReloaded(reason="auto_vram_reconcile"),
        )

    await _emit_load_flow_debug(
        "finalize_loaded_event",
        model_id,
        pid=pid,
        vram_usage_mb=actual_vram,
        ram_usage_mb=actual_ram,
        context_length=context_length,
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
    # Assuming resource_tracker.publish_system_resources() or similar exists for explicit event.
    # If get_system_resources() has a side-effect, this should be documented or renamed.
    # For now, keeping the original call if it's implicitly triggering an event.
    # If not, an explicit event publication is needed here.
    await resource_tracker.get_system_resources()
    # Example: await _publish_event(controller.event_bus, SystemResourcesUpdatedEvent(system_resources))

    logger.info(
        f"✅ Model {model_id} loaded - VRAM: {actual_vram}MB, RAM: {actual_ram}MB"
        + (f", Context: {context_length}" if context_length else "")
    )


def _reconcile_catalog_vram(
    model_id: str,
    actual_vram: int,
    actual_ram: int,
) -> bool:
    """Persist higher measured VRAM into the local operational catalog."""
    try:
        from src.core.catalog.vram_reconciliation import reconcile_max_observed_vram

        return reconcile_max_observed_vram(model_id, actual_vram, actual_ram)
    except Exception as e:
        logger.warning("Failed catalog VRAM reconciliation for %s: %s", model_id, e)
        return False


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
    _cleanup_in_progress.add(model_id)
    await _emit_load_flow_debug("cleanup_failed_worker_start", model_id, reason=reason)
    try:
        await _cleanup_failed_worker_inner(controller, model_id, reason)
    finally:
        _cleanup_in_progress.discard(model_id)
        await _emit_load_flow_debug(
            "cleanup_failed_worker_done", model_id, reason=reason
        )


async def _cleanup_failed_worker_inner(
    controller: "WorkerController", model_id: str, reason: str
):
    resource_tracker = _get_resource_tracker()
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
            await _emit_load_flow_debug(
                "cleanup_supervisor_stop_attempted", model_id, pid=pid
            )
        except Exception as e:
            logger.warning("Supervisor stop failed for %s: %s", model_id, e)
            await _emit_load_flow_debug(
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
                        await _emit_load_flow_debug(
                            "cleanup_force_kill_start", model_id, pid=pid
                        )
                        await controller._lifecycle_manager.kill_pid_tree(pid, model_id)
                        # Re-check if process is dead after force kill
                        process_killed = not psutil.pid_exists(pid)
                        await _emit_load_flow_debug(
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
                    await _emit_load_flow_debug(
                        "cleanup_pid_already_dead", model_id, pid=pid
                    )
            except Exception as e:
                logger.error(
                    "Process existence check or force kill failed for %s (PID %s): %s",
                    model_id,
                    pid,
                    e,
                )
                await _emit_load_flow_debug(
                    "cleanup_pid_check_exception",
                    model_id,
                    pid=pid,
                    error_type=type(e).__name__,
                    error=str(e),
                )
        else:
            process_killed = True
            await _emit_load_flow_debug("cleanup_no_pid", model_id)

        # Clean up state only after process is confirmed dead
        controller._process_state.remove_supervisor(model_id)
        controller._process_state.remove_socket_path(model_id)
        controller._process_state.remove_engine_pid(model_id)

    # Clean up socket file (may exist even if supervisor doesn't)
    await controller._cleanup_socket_file(model_id)

    # Update tracker state (process dead or no supervisor — reconcile SM + status)
    resource_tracker.set_model_not_loaded(model_id, reason)
    await _emit_load_flow_debug(
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


async def handle_load_exception(
    controller: "WorkerController", model_id: str, e: Exception
):
    """Handle exception during model loading with error classification."""
    error_msg, failure_reason = _classify_load_failure(str(e))
    if failure_reason == "oom":
        logger.error(f"❌ OOM error loading {model_id}: {e}")
    elif failure_reason == "insufficient_resources":
        logger.error(f"❌ Resource error loading {model_id}: {e}")
    elif failure_reason == "timeout":
        logger.error(f"❌ Timeout loading {model_id}: {e}")
    elif failure_reason == "missing_file":
        logger.error(f"❌ File not found loading {model_id}: {e}")
    elif failure_reason == "config_error":
        logger.error(f"❌ Configuration error loading {model_id}: {e}")
    else:
        logger.error(f"❌ Error loading {model_id}: {e}")

    _get_resource_tracker().set_model_error(model_id, error_msg)

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
