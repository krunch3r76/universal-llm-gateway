"""ModelLoader class coordinating single-flight model load operations."""

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

from .constants import _LOAD_CACHE_TTL_S, emit_load_gate_debug, get_resource_tracker
from .context_validation import validate_context_availability
from .load_execution import load_model_inner

if TYPE_CHECKING:
    from ...controller import WorkerController

logger = get_logger(__name__)


class ModelLoader:
    """Handles model loading operations. Extracted from WorkerController.

    Single-flight: _pending_loads maps model_id → Future[bool].
    First caller creates the future and runs load_model_inner; followers await it.
    Completed futures stay cached briefly for stampede protection.
    """

    def __init__(self, controller: "WorkerController"):
        self._controller = controller
        self._pending_loads: dict[str, asyncio.Future[bool]] = {}

    async def ensure_model_loaded(
        self, model_id: str, correlation_id: str | None = None
    ) -> bool:
        """Ensure a model is loaded and available for inference."""
        try:
            resource_tracker = get_resource_tracker()
            await emit_load_gate_debug(
                "ensure_enter", model_id, correlation_id=correlation_id
            )

            is_valid, ctx_error = await validate_context_availability(
                self._controller, model_id
            )
            if not is_valid:
                resource_tracker.set_model_error(model_id, ctx_error)
                await emit_load_gate_debug(
                    "context_invalid",
                    model_id,
                    correlation_id=correlation_id,
                    error=ctx_error,
                )
                return False

            already_loaded = await self._controller.is_model_loaded(model_id)
            await emit_load_gate_debug(
                "is_model_loaded_result",
                model_id,
                correlation_id=correlation_id,
                already_loaded=already_loaded,
            )
            if already_loaded:
                return True

            if self._controller.auto_load_on_request:
                logger.info(f"🔄 Auto-loading model: {model_id}")
                await emit_load_gate_debug(
                    "autoload_start", model_id, correlation_id=correlation_id
                )
                loaded = await self.load_model(model_id)
                await emit_load_gate_debug(
                    "autoload_done",
                    model_id,
                    correlation_id=correlation_id,
                    loaded=loaded,
                )
                return loaded

            logger.warning(f"⚠️ Model {model_id} not loaded and auto-load disabled")
            await emit_load_gate_debug(
                "autoload_disabled", model_id, correlation_id=correlation_id
            )
            return False
        except Exception as e:
            get_resource_tracker().set_model_error(model_id, str(e))
            logger.error(
                f"Error ensuring model {model_id} is loaded: {e}",
                exc_info=True,
            )
            await emit_load_gate_debug(
                "ensure_exception",
                model_id,
                correlation_id=correlation_id,
                error_type=type(e).__name__,
                error=str(e),
            )
            return False

    async def load_model(self, model_id: str) -> bool:
        """Load a model with single-flight coalescing + TTL cache."""
        existing = self._pending_loads.get(model_id)
        if existing is not None:
            if not existing.done():
                logger.info(f"🔗 Coalescing onto in-flight load for {model_id}")
                await emit_load_gate_debug("coalesce_inflight", model_id)
                return await existing
            await emit_load_gate_debug("coalesce_cached", model_id)
            return existing.result()

        future: asyncio.Future[bool] = asyncio.Future()
        self._pending_loads[model_id] = future
        try:
            result = await load_model_inner(self, model_id)
            future.set_result(result)
            return result
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            loop = asyncio.get_running_loop()
            _f = future
            loop.call_later(
                _LOAD_CACHE_TTL_S,
                lambda mid=model_id, f=_f: (
                    self._pending_loads.pop(mid, None)
                    if self._pending_loads.get(mid) is f
                    else None
                ),
            )
