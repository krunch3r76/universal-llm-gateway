"""Model loading operations for WorkerController.

Single-flight guarantee: concurrent callers requesting the same model_id
coalesce onto one in-flight load future via `_pending_loads`. Completed
futures remain cached for a short TTL so rapid follow-up callers reuse the
same result instead of stampeding the loader again.
"""

import asyncio
from typing import TYPE_CHECKING, Any

from universal_event_bus.events.debug import emit_debug_event
from universal_logging import get_logger

from . import load_flow, preflight

if TYPE_CHECKING:
    from ..controller import WorkerController


def _get_resource_tracker():
    from src.core.resources import resource_tracker

    return resource_tracker


logger = get_logger(__name__)

_LOAD_CACHE_TTL_S = 10.0
_ENGINE_READY_MAX_ATTEMPTS = 5
_ENGINE_READY_BACKOFF_S = 1.0


async def _emit_load_gate_debug(
    step: str, model_id: str, correlation_id: str | None = None, **extra: Any
) -> None:
    await emit_debug_event(
        "debug.load.gate",
        {
            "step": step,
            "model_id": model_id,
            "correlation_id": correlation_id,
            **extra,
        },
        source="gateway",
    )


class ModelLoader:
    """Handles model loading operations. Extracted from WorkerController.

    Single-flight: _pending_loads maps model_id → Future[bool].
    First caller creates the future and runs _load_model_inner; followers await it.
    Completed futures stay cached briefly for stampede protection.
    """

    def __init__(self, controller: "WorkerController"):
        self._controller = controller
        self._pending_loads: dict[str, asyncio.Future[bool]] = {}

    async def _validate_context_availability(self, model_id: str) -> tuple[bool, str]:
        """Verify resolved n_ctx matches the context encoded in the synthetic model ID.

        For any synthetic model ID with a context suffix, the resolved loader n_ctx
        must equal the suffix value. Emits MODEL_LOAD_CONTEXT_MISMATCH if a mismatch
        is detected.

        Returns (is_valid, error_message).
        """
        from src.core.events.types import ModelLoadContextMismatch

        synthetic_info = self._controller.model_registry._resolve_synthetic_id_info(
            model_id
        )
        is_valid = True
        error_message = ""

        if not synthetic_info:
            return True, ""

        _, requested_ctx, _, _ = synthetic_info
        loader_config = self._controller.model_registry.get_model_loader_config(
            model_id
        )
        if not loader_config:
            is_valid = False
            error_message = f"No loader config found for {model_id}"
        else:
            actual_ctx = loader_config.get("n_ctx")
            if actual_ctx is not None and actual_ctx != requested_ctx:
                logger.error(
                    f"❌ Context mismatch for {model_id}: "
                    f"requested={requested_ctx}, resolved loader n_ctx={actual_ctx}. "
                    f"Check profile loader config and re-run measurement."
                )
                if self._controller.event_bus:
                    from src.core.events.types import ModelLoadContextMismatch

                    await self._controller.event_bus.publish_async_nowait(
                        ModelLoadContextMismatch(
                            model_id=model_id,
                            requested_context=requested_ctx,
                            actual_context=actual_ctx,
                            reason="stale_profile_loader",
                        )
                    )
                is_valid = False
                error_message = (
                    f"Context mismatch: model ID encodes context={requested_ctx} but "
                    f"resolved loader n_ctx={actual_ctx}. "
                    f"Re-run model measurement on this edge node."
                )

        return is_valid, error_message

    async def ensure_model_loaded(
        self, model_id: str, correlation_id: str | None = None
    ) -> bool:
        """Ensure a model is loaded and available for inference."""
        try:
            resource_tracker = _get_resource_tracker()
            await _emit_load_gate_debug(
                "ensure_enter", model_id, correlation_id=correlation_id
            )

            is_valid, ctx_error = await self._validate_context_availability(model_id)
            if not is_valid:
                resource_tracker.set_model_error(model_id, ctx_error)
                await _emit_load_gate_debug(
                    "context_invalid",
                    model_id,
                    correlation_id=correlation_id,
                    error=ctx_error,
                )
                return False

            already_loaded = await self._controller.is_model_loaded(model_id)
            await _emit_load_gate_debug(
                "is_model_loaded_result",
                model_id,
                correlation_id=correlation_id,
                already_loaded=already_loaded,
            )
            if already_loaded:
                return True

            if self._controller.auto_load_on_request:
                logger.info(f"🔄 Auto-loading model: {model_id}")
                await _emit_load_gate_debug(
                    "autoload_start", model_id, correlation_id=correlation_id
                )
                loaded = await self.load_model(model_id)
                await _emit_load_gate_debug(
                    "autoload_done",
                    model_id,
                    correlation_id=correlation_id,
                    loaded=loaded,
                )
                return loaded
            else:
                logger.warning(f"⚠️ Model {model_id} not loaded and auto-load disabled")
                await _emit_load_gate_debug(
                    "autoload_disabled", model_id, correlation_id=correlation_id
                )
                return False
        except Exception as e:
            _get_resource_tracker().set_model_error(model_id, str(e))
            logger.error(
                f"Error ensuring model {model_id} is loaded: {e}",
                exc_info=True,
            )
            await _emit_load_gate_debug(
                "ensure_exception",
                model_id,
                correlation_id=correlation_id,
                error_type=type(e).__name__,
                error=str(e),
            )
            return False

    async def load_model(self, model_id: str) -> bool:
        """Load a model with single-flight coalescing + TTL cache.

        If a load for the same model_id is already in progress, await
        that future instead of starting a duplicate load. Completed futures
        are cached for _LOAD_CACHE_TTL_S to prevent stampede from rapid-fire
        callers (e.g. RAG sending N concurrent embedding requests).
        """
        existing = self._pending_loads.get(model_id)
        if existing is not None:
            if not existing.done():
                logger.info(f"🔗 Coalescing onto in-flight load for {model_id}")
                await _emit_load_gate_debug("coalesce_inflight", model_id)
                return await existing
            await _emit_load_gate_debug("coalesce_cached", model_id)
            return existing.result()

        future: asyncio.Future[bool] = asyncio.Future()
        self._pending_loads[model_id] = future
        try:
            result = await self._load_model_inner(model_id)
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

    async def _load_model_inner(self, model_id: str) -> bool:
        """Actual load logic — called at most once per model_id at a time."""
        try:
            resource_tracker = _get_resource_tracker()

            from src.core.resources.types import ModelStatus

            model_info = resource_tracker.get_model_info(model_id)

            if model_info and model_info.status in (
                ModelStatus.LOADED,
                ModelStatus.BUSY,
            ):
                await _emit_load_gate_debug(
                    "tracker_short_circuit",
                    model_id,
                    tracker_status=model_info.status.value,
                )
                return True

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
                await _emit_load_gate_debug(
                    "preflight_blocked", model_id, error=error_msg
                )
                return False

            if not await self._validate_dependencies(model_id):
                await _emit_load_gate_debug("dependency_invalid", model_id)
                return False

            await load_flow.emit_loading_event(self._controller, model_id, "started")
            await _emit_load_gate_debug("loading_event_started", model_id)

            if self._controller.event_bus:
                try:
                    from src.core.events.types import WorkerLoading

                    req = resource_tracker.get_model_requirements(model_id)
                    estimated_vram = req.get("vram_required_mb") or 0
                    await self._controller.event_bus.publish_async_nowait(
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
                    self._controller,
                    model_id,
                    "failed",
                    "Rejected transition to LOADING (invalid worker state)",
                )
                await _emit_load_gate_debug("loading_transition_rejected", model_id)
                return False
            load_flow.reset_state_machine(model_id)

            vram_before = await load_flow.measure_vram_before(model_id)

            worker_started = await load_flow.start_worker_if_needed(
                self._controller, model_id
            )
            await _emit_load_gate_debug(
                "start_worker_if_needed_done",
                model_id,
                worker_started=worker_started,
            )
            if not worker_started:
                return False

            config_result = await load_flow.send_model_config(
                self._controller, model_id
            )
            if not config_result:
                await _emit_load_gate_debug("send_model_config_failed", model_id)
                return False

            engine_pid = config_result.get("engine_pid")
            if engine_pid is not None:
                self._controller._process_state.set_engine_pid(model_id, engine_pid)
                logger.info("Stored engine_pid=%d for %s", engine_pid, model_id)

            responsive = await load_flow.verify_model_responsive(
                self._controller, model_id
            )
            await _emit_load_gate_debug(
                "verify_model_responsive_done",
                model_id,
                responsive=responsive,
            )
            if not responsive:
                return False

            engine_ready = await self._wait_for_engine_ready(model_id)
            if not engine_ready:
                return False

            resource_tracker.set_model_loaded(model_id)
            await _emit_load_gate_debug("tracker_set_loaded", model_id)

            context_size: int | None = (
                config_result.get("context_size") if config_result else None
            )

            await load_flow.finalize_load(
                self._controller, model_id, vram_before, context_length=context_size
            )
            return True
        except Exception as e:
            error_message = str(e)
            logger.error(
                f"Error loading model {model_id}: {error_message}", exc_info=True
            )
            await load_flow.handle_load_exception(self._controller, model_id, e)
            return False

    async def _validate_dependencies(self, model_id: str) -> bool:
        """Validate worker dependencies."""
        from ..utils import get_python_executable, validate_worker_dependencies

        is_valid, missing = validate_worker_dependencies(
            get_python_executable(self._controller.gateway_config)
        )
        if not is_valid:
            error_msg = f"Missing: {', '.join(missing)}"
            logger.error(f"❌ {error_msg}")
            _get_resource_tracker().set_model_error(model_id, error_msg)
        return is_valid

    async def _wait_for_engine_ready(self, model_id: str) -> bool:
        """Wait for the inference engine to become ready after worker start.

        Bounded retry over check_engine_health() to absorb the warm-up window
        that subprocess-backed engines (llama-server, vLLM) may exhibit after
        the load_model RPC returns. Fail-closed: if readiness cannot be proven
        within the retry budget, the load is treated as failed.

        Returns True if engine is ready, False if all attempts exhausted.
        """
        for attempt in range(1, _ENGINE_READY_MAX_ATTEMPTS + 1):
            ready = await self._controller.check_engine_health(model_id)
            await _emit_load_gate_debug(
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
        resource_tracker = _get_resource_tracker()
        resource_tracker.set_model_error(model_id, error_msg)
        await load_flow.emit_loading_event(
            self._controller, model_id, "failed", error_msg
        )
        await load_flow.cleanup_failed_worker(
            self._controller, model_id, "Engine readiness check failed"
        )
        await _emit_load_gate_debug(
            "engine_health_exhausted",
            model_id,
            attempts=_ENGINE_READY_MAX_ATTEMPTS,
        )
        return False
