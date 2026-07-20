"""Bounded engine readiness polling after worker start during model load."""

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

from .. import load_flow
from .constants import (
    _ENGINE_READY_BACKOFF_S,
    _ENGINE_READY_MAX_ATTEMPTS,
    emit_load_gate_debug,
    get_resource_tracker,
)

if TYPE_CHECKING:
    from .core import ModelLoader

logger = get_logger(__name__)


async def wait_for_engine_ready(loader: "ModelLoader", model_id: str) -> bool:
    """Wait for the inference engine to become ready after worker start."""
    controller = loader._controller
    for attempt in range(1, _ENGINE_READY_MAX_ATTEMPTS + 1):
        ready = await controller.check_engine_health(model_id)
        await emit_load_gate_debug(
            "engine_health_check",
            model_id,
            attempt=attempt,
            max_attempts=_ENGINE_READY_MAX_ATTEMPTS,
            ready=ready,
        )
        if ready:
            logger.info(
                "Engine ready for %s on attempt %d/%d",
                model_id,
                attempt,
                _ENGINE_READY_MAX_ATTEMPTS,
            )
            return True

        if attempt < _ENGINE_READY_MAX_ATTEMPTS:
            logger.info(
                "Engine not ready for %s (attempt %d/%d), retrying in %.1fs",
                model_id,
                attempt,
                _ENGINE_READY_MAX_ATTEMPTS,
                _ENGINE_READY_BACKOFF_S,
            )
            await asyncio.sleep(_ENGINE_READY_BACKOFF_S)

    error_msg = (
        f"Engine readiness check failed after "
        f"{_ENGINE_READY_MAX_ATTEMPTS} attempts for {model_id}"
    )
    logger.error(f"❌ {error_msg}")
    resource_tracker = get_resource_tracker()
    resource_tracker.set_model_error(model_id, error_msg)
    await load_flow.emit_loading_event(controller, model_id, "failed", error_msg)
    await load_flow.cleanup_failed_worker(
        controller, model_id, "Engine readiness check failed"
    )
    await emit_load_gate_debug(
        "engine_health_exhausted",
        model_id,
        attempts=_ENGINE_READY_MAX_ATTEMPTS,
    )
    return False
