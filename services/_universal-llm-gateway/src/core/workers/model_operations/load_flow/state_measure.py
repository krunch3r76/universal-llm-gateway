"""State-machine reset and pre-load VRAM measurement helpers for model loading."""

from .deps import WorkerState, get_resource_tracker, logger


def reset_state_machine(model_id: str):
    """Reset state machine for fresh load."""
    try:
        sm = get_resource_tracker().get_state_machine(model_id)
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
        return (await get_resource_tracker().get_system_resources()).available_vram_mb
    except Exception as e:
        logger.warning("Failed to measure VRAM before loading: %s", e)
        return None
