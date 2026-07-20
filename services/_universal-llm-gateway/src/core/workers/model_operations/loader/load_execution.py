"""Inner load execution steps after preflight and loading.started events."""

from typing import TYPE_CHECKING

from universal_logging import get_logger

from .. import load_flow, load_progress, preflight
from .constants import emit_load_gate_debug, get_resource_tracker
from .dependencies import validate_dependencies
from .engine_ready import wait_for_engine_ready

if TYPE_CHECKING:
    from .core import ModelLoader

logger = get_logger(__name__)


async def load_model_inner(loader: "ModelLoader", model_id: str) -> bool:
    """Actual load logic — called at most once per model_id at a time."""
    controller = loader._controller
    try:
        resource_tracker = get_resource_tracker()

        from src.core.resources.types import ModelStatus

        model_info = resource_tracker.get_model_info(model_id)

        if model_info and model_info.status in (
            ModelStatus.LOADED,
            ModelStatus.BUSY,
        ):
            await emit_load_gate_debug(
                "tracker_short_circuit",
                model_id,
                tracker_status=model_info.status.value,
            )
            return True

        resources_ok, resource_details = await preflight.check_resources_and_block(
            controller, model_id
        )
        if not resources_ok:
            if controller.event_bus and resource_details:
                from src.core.events.types import ModelLoadBlocked

                await controller.event_bus.publish_nowait(
                    ModelLoadBlocked(
                        model_id=model_id,
                        reason=resource_details["reason"],
                        required_vram_mb=resource_details["required_vram_mb"],
                        available_vram_mb=resource_details["available_vram_mb"],
                        required_ram_mb=resource_details["required_ram_mb"],
                        available_ram_mb=resource_details["available_ram_mb"],
                        bypassed_margin=resource_details["bypassed_margin"],
                    )
                )

            model_info = resource_tracker.get_model_info(model_id)
            error_msg = (
                model_info.error_message if model_info else "Insufficient resources"
            )
            await load_flow.emit_loading_event(
                controller, model_id, "failed", error_msg
            )
            await emit_load_gate_debug("preflight_blocked", model_id, error=error_msg)
            return False

        if not await validate_dependencies(controller, model_id):
            await emit_load_gate_debug("dependency_invalid", model_id)
            return False

        await load_flow.emit_loading_event(controller, model_id, "started")
        await emit_load_gate_debug("loading_event_started", model_id)

        heartbeat = load_progress.LoadProgressHeartbeat(controller, model_id)
        await heartbeat.start()
        try:
            return await run_load_after_started(loader, model_id, heartbeat)
        finally:
            await heartbeat.stop()

    except Exception as e:
        error_message = str(e)
        logger.error(f"Error loading model {model_id}: {error_message}", exc_info=True)
        await load_flow.handle_load_exception(controller, model_id, e)
        return False


async def run_load_after_started(
    loader: "ModelLoader",
    model_id: str,
    heartbeat: load_progress.LoadProgressHeartbeat,
) -> bool:
    """Load steps after loading.started and progress heartbeat are active."""
    controller = loader._controller
    resource_tracker = get_resource_tracker()

    if controller.event_bus:
        try:
            from src.core.events.types import WorkerLoading

            req = resource_tracker.get_model_requirements(model_id)
            estimated_vram = req.get("vram_required_mb") or 0
            await controller.event_bus.publish_nowait(
                WorkerLoading(
                    model_id=model_id,
                    estimated_vram_mb=estimated_vram,
                )
            )
        except Exception as e:
            logger.warning("Failed to emit worker.loading: %s", e)

    loading_ok = resource_tracker.set_model_loading(model_id)
    if not loading_ok:
        await load_flow.emit_loading_event(
            controller,
            model_id,
            "failed",
            "Rejected transition to LOADING (invalid worker state)",
        )
        await emit_load_gate_debug("loading_transition_rejected", model_id)
        return False
    load_flow.reset_state_machine(model_id)

    vram_before = await load_flow.measure_vram_before(model_id)

    await heartbeat.emit_phase("worker_start")
    worker_started = await load_flow.start_worker_if_needed(controller, model_id)
    await emit_load_gate_debug(
        "start_worker_if_needed_done",
        model_id,
        worker_started=worker_started,
    )
    if not worker_started:
        return False

    await heartbeat.emit_phase("config")
    config_result = await load_flow.send_model_config(controller, model_id)
    if not config_result:
        await emit_load_gate_debug("send_model_config_failed", model_id)
        return False

    engine_pid = config_result.get("engine_pid")
    if engine_pid is not None:
        controller._process_state.set_engine_pid(model_id, engine_pid)
        logger.info("Stored engine_pid=%d for %s", engine_pid, model_id)

    responsive = await load_flow.verify_model_responsive(controller, model_id)
    await emit_load_gate_debug(
        "verify_model_responsive_done",
        model_id,
        responsive=responsive,
    )
    if not responsive:
        return False

    await heartbeat.emit_phase("engine_wait")
    engine_ready = await wait_for_engine_ready(loader, model_id)
    if not engine_ready:
        return False

    resource_tracker.set_model_loaded(model_id)
    await emit_load_gate_debug("tracker_set_loaded", model_id)

    context_size: int | None = (
        config_result.get("context_size") if config_result else None
    )

    await heartbeat.emit_phase("finalize")
    await load_flow.finalize_load(
        controller, model_id, vram_before, context_length=context_size
    )
    return True
