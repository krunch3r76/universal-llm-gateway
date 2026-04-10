"""Core resource tracker for model VRAM/RAM and inference state.

ResourceTracker provides comprehensive tracking of:
- VRAM/RAM usage per model
- Model loading/unloading states via per-variant state machines
- Inference start/stop times
- System resource availability

Key spaces:
- tracking_key (preserves -hybrid): state machines, ModelResourceInfo, events
- process_key (strips -hybrid): ProcessState supervisors, sockets, PIDs

The VariantRegistry bridges the two key spaces so unloads check all variants
sharing a physical process before tearing it down.

Thread Safety: Not needed. All access from single-threaded async event loop.
"""

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

from model_id import ModelId
from universal_logging import get_logger

from src.core.workers.state_machine import WorkerState, WorkerStateMachine

from .cleanup import cleanup_stale_models as _cleanup_stale_models
from .events import emit_inference_completed, emit_inference_started
from .hardware import (
    get_process_gpu_memory,
    get_process_ram_usage,
    get_ram_info,
    get_vram_info,
)
from .tracker_queries import (
    get_all_models_info as _get_all_models_info,
)
from .tracker_queries import (
    get_busy_models as _get_busy_models,
)
from .tracker_queries import (
    get_loaded_models as _get_loaded_models,
)
from .tracker_queries import (
    get_model_info as _get_model_info,
)
from .tracker_queries import (
    get_model_requirements_wrapper,
    get_model_resources_from_config_wrapper,
)
from .tracker_queries import (
    get_operations_in_progress as _get_operations_in_progress,
)
from .tracker_queries import (
    get_state_machine_status as _get_state_machine_status,
)
from .tracker_queries import (
    get_system_resources as _get_system_resources,
)
from .transitions import (
    handle_error_state_recovery,
    transition_to_idle,
    transition_to_loading,
    update_model_idle_status_async,
)
from .types import (
    WORKER_TO_MODEL_STATUS,
    ModelResourceInfo,
    ModelStatus,
    SystemResourceInfo,
)
from .variant_registry import VariantRegistry

logger = get_logger(__name__)

_IN_USE_STATES = frozenset({WorkerState.BUSY, WorkerState.LOADING})


def _tracking_key(model_id: str | ModelId) -> str:
    """Per-variant key preserving -hybrid for state machines and ModelResourceInfo."""
    if isinstance(model_id, ModelId):
        return model_id.tracking_key
    return ModelId.parse(model_id).tracking_key


def _process_key(model_id: str | ModelId) -> str:
    """Shared-process key stripping -hybrid for supervisor/socket/PID lookups."""
    if isinstance(model_id, ModelId):
        return model_id.process_key
    return ModelId.parse(model_id).process_key


class ResourceTracker:
    """Resource tracking for model lifecycle and resource consumption.

    Keys in _models and _state_machines are tracking_keys (preserve -hybrid)
    so each variant has independent lifecycle state. The _variant_registry
    maps process_keys → tracking_keys for unload gating.
    """

    def __init__(self, event_bus: Any | None = None):
        self.logger = get_logger(__name__)
        self._models: dict[str, ModelResourceInfo] = {}
        self._state_machines: dict[str, WorkerStateMachine] = {}
        self._variant_registry = VariantRegistry()
        self._system_info: SystemResourceInfo | None = None
        self._initialized = False
        self.event_bus = event_bus
        self._initialize_system_resources()

    def set_event_bus(self, event_bus: Any) -> None:
        """Set the EventBus for publishing events."""
        self.event_bus = event_bus

    def _initialize_system_resources(self) -> None:
        try:
            ram_info = get_ram_info()
            vram_info = get_vram_info()
            self._system_info = SystemResourceInfo(
                total_vram_mb=vram_info["total_vram_mb"],
                available_vram_mb=vram_info["available_vram_mb"],
                total_ram_mb=ram_info["total_ram_mb"],
                available_ram_mb=ram_info["available_ram_mb"],
            )
            self._initialized = True
            self.logger.info(
                f"Resource tracker initialized - VRAM: {vram_info['total_vram_mb']}MB, "
                f"RAM: {ram_info['total_ram_mb']}MB"
            )
        except Exception as e:
            self.logger.exception(f"Failed to initialize resource tracker: {e}")
            self._initialized = False

    def _emit_state_change(
        self,
        model_id: str | ModelId,
        from_status: ModelStatus,
        to_status: ModelStatus,
        error_message: str | None,
    ) -> None:
        """Schedule async emission of model.state.changed event (non-blocking)."""
        try:
            loop = asyncio.get_running_loop()
            if loop.is_closed():
                return
        except RuntimeError:
            return

        payload = {
            "model_id": str(model_id),
            "from": from_status.value,
            "to": to_status.value,
            "error": error_message,
        }

        async def _publish() -> None:
            try:
                from universal_event_bus.events.debug import emit_debug_event

                await emit_debug_event(
                    "model.state.changed",
                    payload,
                    source="gateway",
                    role="observation",
                    scope="node",
                )
            except Exception as exc:
                self.logger.debug(
                    "Failed to emit model.state.changed for %s: %s",
                    model_id,
                    exc,
                )

        loop.create_task(_publish())

    def _on_sm_transition(
        self,
        key: str,
        model_id: str,
        from_state: WorkerState,
        to_state: WorkerState,
        reason: str,
        metadata: dict | None,
    ) -> None:
        """Handle bookkeeping and event emission after a state machine transition.

        Uses tracking_key for _models lookup. Events use the original model_id
        string so external consumers see the full ID including -hybrid.
        """
        if key in self._models:
            m = self._models[key]
            m.last_updated = time.time()
            if from_state == WorkerState.ERROR:
                m.error_message = None
            if to_state == WorkerState.LOADED and m.load_time is None:
                m.load_time = time.time()

        from_status = WORKER_TO_MODEL_STATUS[from_state]
        to_status = WORKER_TO_MODEL_STATUS[to_state]
        if from_status != to_status:
            error_msg = (
                (metadata or {}).get("error_message")
                if to_status == ModelStatus.ERROR
                else None
            )
            self._emit_state_change(model_id, from_status, to_status, error_msg)

    # -------------------------------------------------------------------------
    # Model Registration
    # -------------------------------------------------------------------------

    def register_model(self, model_id: str | ModelId) -> None:
        """Register a new model variant for tracking with its own state machine.

        Creates a per-variant SM keyed by tracking_key and registers the
        variant under its process_key in the VariantRegistry.
        """
        tkey = _tracking_key(model_id)
        pkey = _process_key(model_id)
        if tkey not in self._models:
            model_str = str(model_id)

            def _on_transition(
                from_state: WorkerState,
                to_state: WorkerState,
                reason: str,
                meta: dict | None,
            ) -> None:
                self._on_sm_transition(
                    tkey, model_str, from_state, to_state, reason, meta
                )

            sm = WorkerStateMachine(
                worker_id=model_str,
                initial_state=WorkerState.UNINITIALIZED,
                on_transition=_on_transition,
            )
            info = ModelResourceInfo(model_id=model_str)
            info._sm = sm
            self._models[tkey] = info
            self._state_machines[tkey] = sm
            self._variant_registry.register(pkey, tkey)
            self.logger.info(f"Registered model for tracking: {model_id} (tkey={tkey})")

    def unregister_model(self, model_id: str | ModelId) -> None:
        """Unregister a model variant from tracking."""
        tkey = _tracking_key(model_id)
        self._models.pop(tkey, None)
        self._state_machines.pop(tkey, None)
        self._variant_registry.unregister(tkey)
        self.logger.info(f"Unregistered model from tracking: {model_id}")

    # -------------------------------------------------------------------------
    # Process-level queries (for unload gating)
    # -------------------------------------------------------------------------

    def is_process_in_use(self, model_id: str | ModelId) -> bool:
        """True if any variant sharing this model's worker process is busy/loading."""
        pkey = _process_key(model_id)
        return self._variant_registry.is_process_in_use(
            pkey, self._state_machines, _IN_USE_STATES
        )

    def describe_busy_variants(self, model_id: str | ModelId) -> list[str]:
        """Human-readable list of busy variants on this model's process."""
        pkey = _process_key(model_id)
        return self._variant_registry.describe_busy_variants(
            pkey, self._state_machines, _IN_USE_STATES
        )

    # -------------------------------------------------------------------------
    # Model Status Management
    # -------------------------------------------------------------------------

    def set_model_loading(self, model_id: str | ModelId) -> bool:
        """Mark a model as loading. Resets ERROR state automatically.

        Returns:
            False if the state machine rejected transition to LOADING (abort load).
        """
        model_str = str(model_id)
        tkey = _tracking_key(model_id)
        handle_error_state_recovery(
            self._state_machines,
            self._models,
            tkey,
            model_str,
        )
        if tkey not in self._models:
            self.register_model(model_id)
        return transition_to_loading(self._state_machines, tkey, model_str)

    def set_model_loaded(
        self, model_id: str | ModelId, process_pid: int | None = None
    ) -> None:
        """Mark model loaded; records hardware VRAM when pid is available.

        Propagates LOADED to all sibling variants sharing the same physical
        worker process:
        - LOADING siblings: normal LOADING → LOADED transition.
        - UNLOADED / UNINITIALIZED siblings: the shared process is now alive
          but the sibling SM was never advanced (e.g. federation load used the
          base routing_key while a -hybrid SM is UNLOADED from a prior eviction).
          Advance through LOADING → LOADED so set_model_busy() can succeed
          without an unnecessary redundant reload.
        """
        tkey = _tracking_key(model_id)
        pkey = _process_key(model_id)
        if tkey in self._state_machines:
            self._state_machines[tkey].transition(
                WorkerState.LOADED, reason="model_loaded_successfully"
            )
        if tkey in self._models:
            if process_pid:
                self._models[tkey].process_pid = process_pid
                measured = get_process_gpu_memory(process_pid)
                if measured is not None and measured > 0:
                    self._models[tkey].measured_vram_mb = measured
                    self.logger.info(
                        "Measured VRAM for %s: %dMB (catalog estimate: %dMB)",
                        model_id,
                        measured,
                        self._models[tkey].vram_usage_mb,
                    )

        _needs_load_advance = frozenset(
            {WorkerState.UNLOADED, WorkerState.UNINITIALIZED}
        )
        for sibling_tkey in self._variant_registry.get_variants(pkey):
            if sibling_tkey == tkey:
                continue
            sm = self._state_machines.get(sibling_tkey)
            if sm is None:
                continue
            if sm.current_state == WorkerState.LOADING:
                sm.transition(WorkerState.LOADED, reason="sibling_loaded")
            elif sm.current_state in _needs_load_advance:
                # Shared process is now alive; advance sibling SM so that
                # set_model_busy() succeeds without a redundant reload.
                self.logger.info(
                    "Advancing sibling SM %s from %s → LOADED (shared process loaded)",
                    sibling_tkey,
                    sm.current_state.value,
                )
                if sm.transition(WorkerState.LOADING, reason="sibling_process_loaded"):
                    sm.transition(WorkerState.LOADED, reason="sibling_process_loaded")

    async def set_model_busy(
        self, model_id: str | ModelId, request_id: str = ""
    ) -> None:
        """Mark a model variant as busy (processing inference).

        Emits INFERENCE_STARTED and REQUEST_INFERENCE_STARTED events.
        Raises RuntimeError if the variant's SM cannot transition to BUSY
        (e.g. process is unloading) so track_inference can abort.
        """
        tkey = _tracking_key(model_id)
        model_str = str(model_id)
        if tkey in self._state_machines:
            success = self._state_machines[tkey].transition(
                WorkerState.BUSY,
                reason="inference_started",
                guard=lambda: (
                    self._state_machines[tkey].current_state == WorkerState.LOADED
                ),
            )
            if not success:
                current = self._state_machines[tkey].current_state.value
                msg = (
                    f"Cannot mark {model_id} as busy — variant SM in {current}, "
                    f"expected LOADED"
                )
                self.logger.warning(msg)
                raise RuntimeError(msg)
        if tkey in self._models:
            self._models[tkey].current_inference_start = time.time()
            self.logger.debug(f"Model {model_id} marked as busy")
            await emit_inference_started(self.event_bus, model_str, request_id)

    async def set_model_idle(self, model_id: str | ModelId) -> None:
        """Mark a model variant as idle (finished inference or cancelled)."""
        tkey = _tracking_key(model_id)
        model_str = str(model_id)
        transition_to_idle(self._state_machines, tkey, model_str)
        await update_model_idle_status_async(
            self._models,
            tkey,
            model_str,
            self.event_bus,
        )

    def set_model_inference_state(
        self, model_id: str | ModelId, inference_state: str
    ) -> None:
        """Set inference state ('token_counting' or 'generating')."""
        tkey = _tracking_key(model_id)
        if tkey in self._models:
            if self._models[tkey].status == ModelStatus.BUSY:
                self._models[tkey].inference_state = inference_state
                self._models[tkey].last_updated = time.time()
                self.logger.debug(
                    f"Model {model_id} inference state: {inference_state}"
                )
            else:
                self.logger.warning(
                    f"Cannot set inference state for {model_id} - not busy"
                )

    def set_model_error(self, model_id: str | ModelId, error_message: str) -> None:
        """Mark a model as having an error."""
        tkey = _tracking_key(model_id)
        if tkey not in self._models:
            self.register_model(model_id)
        current_info = self._models[tkey]
        if current_info.status == ModelStatus.ERROR:
            self.logger.warning(
                f"Model {model_id} already in ERROR. Updating error message from "
                f"'{current_info.error_message}' to '{error_message}'"
            )
        if tkey in self._state_machines:
            self._state_machines[tkey].set_error(error_message)
        self._models[tkey].error_message = error_message

    def get_model_error(self, model_id: str | ModelId) -> str | None:
        """Get the error message for a model when its status is ERROR."""
        tkey = _tracking_key(model_id)
        if tkey in self._models:
            info = self._models[tkey]
            if info.status == ModelStatus.ERROR:
                return info.error_message
        return None

    def set_model_unloading(self, model_id: str | ModelId) -> None:
        """Mark all variants sharing the physical process as unloading."""
        pkey = _process_key(model_id)
        for vkey in self._variant_registry.get_variants(pkey):
            if vkey not in self._models:
                continue
            if vkey not in self._state_machines:
                continue
            sm = self._state_machines[vkey]
            if sm.current_state in (
                WorkerState.LOADED,
                WorkerState.BUSY,
                WorkerState.ERROR,
            ):
                if not sm.transition(
                    WorkerState.UNLOADING, reason="model_unloading_started"
                ):
                    self.logger.warning(
                        "Failed to transition %s to UNLOADING (SM=%s)",
                        vkey,
                        sm.current_state.value,
                    )

    def set_model_not_loaded(self, model_id: str | ModelId, reason: str) -> None:
        """Mark all variants on this process as not loaded.

        Clears load_time, process_pid, and inference fields to prevent
        cross-session data leaks.
        """
        pkey = _process_key(model_id)
        for vkey in self._variant_registry.get_variants(pkey):
            if vkey not in self._state_machines:
                continue
            sm = self._state_machines[vkey]
            if sm.current_state == WorkerState.ERROR:
                if not sm.clear_error(reason):
                    sm.force_unloaded(reason)
            elif sm.current_state == WorkerState.UNLOADING:
                if not sm.transition(WorkerState.UNLOADED, reason=reason):
                    sm.force_unloaded(reason)
            elif sm.current_state not in (
                WorkerState.UNINITIALIZED,
                WorkerState.UNLOADED,
            ):
                sm.force_unloaded(reason)

            if vkey in self._models:
                m = self._models[vkey]
                m.load_time = None
                m.process_pid = None
                m.current_inference_start = None
                m.error_message = None
                m.measured_vram_mb = None

    async def force_model_idle(self, model_id: str | ModelId, reason: str) -> bool:
        """Force a model to idle state (for cancellation)."""
        tkey = _tracking_key(model_id)
        model_str = str(model_id)
        sm_success = False
        if tkey in self._state_machines:
            sm_success = self._state_machines[tkey].force_idle(reason)
        if tkey in self._models:
            m = self._models[tkey]
            m.last_inference_end = m.last_inference_time = time.time()
            m.current_inference_start = m.inference_state = None
            self.logger.info(
                f"✅ Model {model_id} forced idle (reason: {reason}, sm={sm_success})"
            )
            await emit_inference_completed(
                self.event_bus, model_str, m.last_inference_end
            )
            return True
        self.logger.warning(f"⚠️ Cannot force idle for {model_id} - not in tracker")
        return False

    # -------------------------------------------------------------------------
    # Inference Tracking
    # -------------------------------------------------------------------------

    @asynccontextmanager
    async def track_inference(self, model_id: str | ModelId, request_id: str = ""):
        """Context manager to track inference lifecycle.

        Raises RuntimeError at entry if the variant SM cannot transition to
        BUSY (e.g. shared process is unloading). The caller should catch this
        and return 503 to the client.
        """
        t0 = time.monotonic()
        self.logger.info(
            f"⏱️ track_inference ENTER: model={model_id} request={request_id}"
        )
        try:
            await self.set_model_busy(model_id, request_id)
            yield
        finally:
            elapsed_ms = (time.monotonic() - t0) * 1000
            self.logger.info(
                f"⏱️ track_inference EXIT: model={model_id} request={request_id} "
                f"held={elapsed_ms:.0f}ms"
            )
            await self.set_model_idle(model_id)

    # -------------------------------------------------------------------------
    # Resource Updates
    # -------------------------------------------------------------------------

    def update_model_resources(
        self, model_id: str | ModelId, vram_mb: int, ram_mb: int
    ) -> None:
        """Update resource usage for a model."""
        tkey = _tracking_key(model_id)
        if tkey not in self._models:
            self.register_model(model_id)
        self._models[tkey].vram_usage_mb = vram_mb or 0
        self._models[tkey].ram_usage_mb = ram_mb or 0
        self._models[tkey].last_updated = time.time()
        self.logger.debug(f"Updated {model_id}: VRAM={vram_mb}MB, RAM={ram_mb}MB")

    def update_model_last_inference_time(
        self, model_id: str | ModelId, ts: float | None = None
    ) -> None:
        tkey = _tracking_key(model_id)
        if tkey in self._models:
            self._models[tkey].last_inference_time = ts or time.time()
            self._models[tkey].last_updated = time.time()

    def measure_and_update_model_resources(
        self,
        model_id: str | ModelId,
        pid: int,
        fallback_vram: int = 0,
        fallback_ram: int = 0,
    ) -> tuple[int, int]:
        """Measure actual resource usage and update tracking."""
        actual_vram = get_process_gpu_memory(pid)
        actual_ram = get_process_ram_usage(pid)
        final_vram = actual_vram or fallback_vram
        final_ram = actual_ram or fallback_ram
        if (
            actual_vram is not None
            and fallback_vram > 0
            and abs(actual_vram - fallback_vram) > 100
        ):
            self.logger.info(
                f"📊 {model_id} VRAM: catalog={fallback_vram}MB, actual={actual_vram}MB"
            )
        self.update_model_resources(model_id, final_vram, final_ram)
        return final_vram, final_ram

    def get_current_process_resources(
        self,
        pid: int,
        model_id: str | ModelId | None = None,
        baseline_vram_mb: int | None = None,
        baseline_ram_mb: int | None = None,
        log_growth_threshold_mb: int = 200,
    ) -> tuple[int | None, int | None]:
        """Get current VRAM and RAM usage for a process."""
        vram = get_process_gpu_memory(pid)
        ram = get_process_ram_usage(pid)
        if (
            model_id
            and baseline_vram_mb
            and vram
            and vram > baseline_vram_mb + log_growth_threshold_mb
        ):
            self.logger.debug(f"📈 {model_id} VRAM growth: {baseline_vram_mb}→{vram}MB")
        return vram, ram

    # -------------------------------------------------------------------------
    # Query Methods (delegated)
    # -------------------------------------------------------------------------

    def get_model_info(self, model_id: str | ModelId) -> ModelResourceInfo | None:
        return _get_model_info(self, model_id)

    def get_all_models_info(self) -> dict[str, ModelResourceInfo]:
        return _get_all_models_info(self)

    def get_loaded_models(self) -> list[str]:
        return _get_loaded_models(self)

    def get_busy_models(self) -> list[str]:
        return _get_busy_models(self)

    def get_operations_in_progress(self) -> dict[str, list[str]]:
        return _get_operations_in_progress(self)

    def get_state_machine(self, model_id: str | ModelId) -> WorkerStateMachine | None:
        return self._state_machines.get(_tracking_key(model_id))

    def get_state_machine_state(self, model_id: str | ModelId) -> str:
        sm = self._state_machines.get(_tracking_key(model_id))
        return sm.current_state.value if sm else "none"

    def get_state_machine_status(self, model_id: str | ModelId) -> dict | None:
        return _get_state_machine_status(self, model_id)

    async def get_system_resources(self) -> SystemResourceInfo:
        return await _get_system_resources(self)

    def get_model_requirements(self, model_id: str | ModelId) -> dict[str, Any]:
        return get_model_requirements_wrapper(model_id)

    def get_model_resources_from_config(
        self, model_id: str | ModelId
    ) -> tuple[int | None, int | None]:
        return get_model_resources_from_config_wrapper(model_id)

    # -------------------------------------------------------------------------
    # Cleanup (delegated)
    # -------------------------------------------------------------------------

    def cleanup_stale_models(self) -> list[str]:
        return _cleanup_stale_models(self)


# Global resource tracker instance
resource_tracker = ResourceTracker()
