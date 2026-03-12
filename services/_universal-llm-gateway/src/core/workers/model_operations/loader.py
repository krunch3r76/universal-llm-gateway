"""Model loading operations for WorkerController.

Single-flight guarantee: concurrent callers requesting the same model_id
coalesce onto one in-flight load future via _pending_loads.
"""

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

from . import load_flow, preflight

if TYPE_CHECKING:
    from ..controller import WorkerController


def _get_resource_tracker():
    from src.core.resources import resource_tracker

    return resource_tracker


logger = get_logger(__name__)
structured_logger = get_logger("universal_llm_gateway.model_loader")


class ModelLoader:
    """Handles model loading operations. Extracted from WorkerController.

    Single-flight: _pending_loads maps model_id → Future[bool].
    First caller creates the future and runs _load_model_inner; followers await it.
    """

    def __init__(self, controller: "WorkerController"):
        self._controller = controller
        self._pending_loads: dict[str, asyncio.Future[bool]] = {}

    async def _validate_context_availability(self, model_id: str) -> tuple[bool, str]:
        """Verify resolved n_ctx matches the context encoded in the synthetic model ID.

        ∀ synthetic model ID with context suffix: resolved loader n_ctx must equal
        the suffix value. Emits MODEL_LOAD_CONTEXT_MISMATCH if a stale value is present
        (after _select_profile_loader has already corrected it in Task 1 — this is a
        belt-and-suspenders guard for future regressions).

        Returns (is_valid, error_message).
        """
        from src.core.events.types import ModelLoadContextMismatch

        synthetic_info = self._controller.model_registry._resolve_synthetic_id_info(
            model_id
        )
        if not synthetic_info:
            return True, ""

        _, requested_ctx, _, _ = synthetic_info
        loader_config = self._controller.model_registry.get_model_loader_config(
            model_id
        )
        if not loader_config:
            return False, f"No loader config found for {model_id}"

        actual_ctx = loader_config.get("n_ctx")
        if actual_ctx is None:
            # vLLM or config-less model — no context validation needed
            return True, ""

        if actual_ctx != requested_ctx:
            # Should not happen after profile loader has selected the correct config.
            # If it does, something else in the stack is overriding n_ctx.
            logger.error(
                f"❌ Context mismatch for {model_id}: "
                f"requested={requested_ctx}, resolved loader n_ctx={actual_ctx}. "
                f"Check profile loader config and re-run measurement."
            )
            if self._controller.event_bus:
                await self._controller.event_bus.publish_async_nowait(
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

    async def ensure_model_loaded(self, model_id: str) -> bool:
        """Ensure a model is loaded and available for inference."""
        try:
            resource_tracker = _get_resource_tracker()

            is_valid, ctx_error = await self._validate_context_availability(model_id)
            if not is_valid:
                resource_tracker.set_model_error(model_id, ctx_error)
                return False

            resource_tracker.register_model(model_id)

            if await self._controller.is_model_loaded(model_id):
                return True

            if self._controller.auto_load_on_request:
                logger.info(f"🔄 Auto-loading model: {model_id}")
                return await self.load_model(model_id)
            else:
                logger.warning(f"⚠️ Model {model_id} not loaded and auto-load disabled")
                return False
        except Exception as e:
            _get_resource_tracker().set_model_error(model_id, str(e))
            logger.error(
                f"Error ensuring model {model_id} is loaded: {e}", exc_info=True
            )
            return False

    async def load_model(self, model_id: str) -> bool:
        """Load a model with single-flight coalescing.

        If a load for the same model_id is already in progress, await
        that future instead of starting a duplicate load.
        """
        existing = self._pending_loads.get(model_id)
        if existing is not None and not existing.done():
            logger.info(f"🔗 Coalescing onto in-flight load for {model_id}")
            return await existing

        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._pending_loads[model_id] = future
        try:
            result = await self._load_model_inner(model_id)
            future.set_result(result)
            return result
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            self._pending_loads.pop(model_id, None)

    async def _load_model_inner(self, model_id: str) -> bool:
        """Actual load logic — called at most once per model_id at a time."""
        try:
            resource_tracker = _get_resource_tracker()
            logger.info(f"📦 Loading model: {model_id}")

            resources_ok, resource_details = await preflight.check_resources_and_block(
                self._controller, model_id
            )
            if not resources_ok:
                if self._controller.event_bus and resource_details:
                    from src.core.events.types import ModelLoadBlocked

                    await self._controller.event_bus.publish_async_nowait(
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
                    self._controller, model_id, "failed", error_msg
                )
                return False

            if not await self._validate_dependencies(model_id):
                return False

            await load_flow.emit_loading_event(self._controller, model_id, "started")
            resource_tracker.register_model(model_id)
            resource_tracker.set_model_loading(model_id)
            load_flow.reset_state_machine(model_id)

            vram_before = await load_flow.measure_vram_before(model_id)
            if not await load_flow.start_worker_if_needed(self._controller, model_id):
                return False

            config_result = await load_flow.send_model_config(
                self._controller, model_id
            )
            if not config_result:
                return False

            if not await load_flow.verify_model_responsive(self._controller, model_id):
                return False

            resource_tracker.set_model_loaded(model_id)

            context_size = config_result.get("context_size") if config_result else None

            await load_flow.finalize_load(
                self._controller, model_id, vram_before, context_length=context_size
            )
            return True
        except Exception as e:
            logger.error(f"Error loading model {model_id}: {e}", exc_info=True)
            await load_flow.handle_load_exception(self._controller, model_id, e)
            return False

    async def _validate_dependencies(self, model_id: str) -> bool:
        """Validate worker dependencies."""
        from ..utils import get_python_executable, validate_worker_dependencies

        is_valid, missing = validate_worker_dependencies(
            get_python_executable(self._controller.gateway_config)
        )
        if not is_valid:
            logger.error(f"❌ Missing dependencies: {missing}")
            _get_resource_tracker().set_model_error(
                model_id, f"Missing: {', '.join(missing)}"
            )
            return False
        return True
