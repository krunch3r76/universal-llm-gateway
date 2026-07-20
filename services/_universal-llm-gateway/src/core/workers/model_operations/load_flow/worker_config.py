"""Model configuration dispatch and post-load responsiveness checks for workers."""

from typing import TYPE_CHECKING, Any

from .cleanup import cleanup_failed_worker
from .deps import emit_load_flow_debug, get_resource_tracker, logger
from .events import emit_loading_event
from .failure_classify import resolve_load_failure

if TYPE_CHECKING:
    from ...controller import WorkerController


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
        error_msg, _ = resolve_load_failure(
            model_id, "No supervisor available during model config send"
        )
        logger.error(
            "No supervisor for %s during model config send: %s", model_id, error_msg
        )
        get_resource_tracker().set_model_error(model_id, error_msg)
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
    await emit_load_flow_debug("verify_process_alive", model_id, alive=alive)
    if not alive:
        error_msg, _ = resolve_load_failure(model_id, "Model not responsive")
        logger.error("❌ Model %s not responsive: %s", model_id, error_msg)
        get_resource_tracker().set_model_error(model_id, error_msg)
        await emit_loading_event(controller, model_id, "failed", error_msg)
        await cleanup_failed_worker(controller, model_id, "Unresponsive process")
        return False
    return True
