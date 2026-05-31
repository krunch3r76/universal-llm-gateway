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
from typing import Any

from model_id import ModelId
from universal_logging import get_logger

from src.core.workers.state_machine import WorkerState, WorkerStateMachine

from .cleanup import cleanup_stale_models as _cleanup_stale_models
from .hardware import (
    get_process_gpu_memory,
    get_process_ram_usage,
    get_ram_info,
    get_vram_info,
)
from .tracker_keys import _process_key, _tracking_key
from .tracker_mixin_model_status import _ResourceTrackerMixinModelStatus
from .tracker_mixin_unload_inference import _ResourceTrackerMixinUnloadAndInference
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
from .types import (
    ModelResourceInfo,
    ModelStatus,
    SystemResourceInfo,
    worker_to_model_status,
)
from .variant_registry import VariantRegistry

logger = get_logger(__name__)

_IN_USE_STATES = frozenset({WorkerState.BUSY, WorkerState.LOADING})


class ResourceTracker(
    _ResourceTrackerMixinModelStatus,
    _ResourceTrackerMixinUnloadAndInference,
):
    """Resource tracking for model lifecycle and resource consumption.

    Keys in _models and _state_machines are tracking_keys (preserve -hybrid)
    so each variant has independent lifecycle state. The _variant_registry
    maps process_keys → tracking_keys for unload gating.
    """

    def __init__(self, event_bus: Any | None = None):
        self.logger = get_logger(__name__)
        self._models: dict[str, ModelResourceInfo] = {}
        self._state_machines: dict[str, WorkerStateMachine] = {}
        self._busy_count: dict[str, int] = {}
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

        status_map = worker_to_model_status()
        from_status = status_map[from_state]
        to_status = status_map[to_state]
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
        self._busy_count.pop(tkey, None)
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
