"""Context availability validation for synthetic model IDs before load attempts."""

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from ...controller import WorkerController

logger = get_logger(__name__)


async def validate_context_availability(
    controller: "WorkerController", model_id: str
) -> tuple[bool, str]:
    """Verify resolved n_ctx matches the context encoded in the synthetic model ID."""
    synthetic_info = controller.model_registry._resolve_synthetic_id_info(model_id)
    if not synthetic_info:
        return True, ""

    _, requested_ctx, _, _ = synthetic_info
    loader_config = controller.model_registry.get_model_loader_config(model_id)
    if not loader_config:
        return False, f"No loader config found for {model_id}"

    actual_ctx = loader_config.get("n_ctx")
    if actual_ctx is not None and actual_ctx != requested_ctx:
        logger.error(
            f"❌ Context mismatch for {model_id}: "
            f"requested={requested_ctx}, resolved loader n_ctx={actual_ctx}. "
            f"Check profile loader config and re-run measurement."
        )
        if controller.event_bus:
            from src.core.events.types import ModelLoadContextMismatch

            await controller.event_bus.publish_nowait(
                ModelLoadContextMismatch(
                    model_id=model_id,
                    requested_context=requested_ctx,
                    actual_context=actual_ctx,
                    reason="stale_profile_loader",
                )
            )
        return False, (
            f"Context mismatch: model ID encodes context={requested_ctx} but "
            f"resolved loader n_ctx={actual_ctx}. "
            f"Re-run model measurement on this edge node."
        )

    return True, ""
