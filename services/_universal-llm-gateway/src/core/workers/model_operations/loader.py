"""Model loading operations for WorkerController."""

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
    """Handles model loading operations. Extracted from WorkerController."""

    def __init__(self, controller: "WorkerController"):
        self._controller = controller

    async def ensure_model_loaded(self, model_id: str) -> bool:
        """Ensure a model is loaded and available for inference."""
        try:
            resource_tracker = _get_resource_tracker()
            resource_tracker.register_model(model_id)

            if await self._controller.is_model_loaded(model_id):
                # Model already loaded - do NOT call set_model_loaded here
                # to avoid "loaded → loaded" invalid transition
                return True

            if self._controller.auto_load_on_request:
                logger.info(f"🔄 Auto-loading model: {model_id}")
                return await self.load_model(model_id)
            else:
                logger.warning(f"⚠️ Model {model_id} not loaded and auto-load disabled")
                return False
        except Exception as e:
            _get_resource_tracker().set_model_error(model_id, str(e))
            logger.error(f"Error ensuring model {model_id} is loaded: {e}")
            return False

    async def load_model(self, model_id: str) -> bool:
        """Load a model with resource tracking and event publishing."""
        try:
            resource_tracker = _get_resource_tracker()
            logger.info(f"📦 Loading model: {model_id}")

            # Recommendation #1 & #7: Check resources and emit events in load layer
            resources_ok, resource_details = await preflight.check_resources_and_block(
                self._controller, model_id
            )
            if not resources_ok:
                # Emit MODEL_LOAD_BLOCKED event for observability (circuit breaker)
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

                # Emit MODEL_LOAD_FAILED for Stargate notification (fast-fail)
                error_msg = (
                    resource_tracker.get_model_info(model_id).error_message
                    if resource_tracker.get_model_info(model_id)
                    else "Insufficient resources"
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

            # Send config and capture response with context_size
            config_result = await load_flow.send_model_config(
                self._controller, model_id
            )
            if not config_result:
                return False

            if not await load_flow.verify_model_responsive(self._controller, model_id):
                return False

            resource_tracker.set_model_loaded(model_id)

            # Extract context_size from response (default to None if missing)
            context_size = config_result.get("context_size") if config_result else None

            # Pass context_size to finalize_load
            await load_flow.finalize_load(
                self._controller, model_id, vram_before, context_length=context_size
            )
            return True
        except Exception as e:
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
