"""Core resource tracker for model VRAM/RAM and inference state.

ResourceTracker provides comprehensive tracking of:
- VRAM/RAM usage per model
- Model loading/unloading states
- Inference start/stop times
- System resource availability

Thread Safety: Not needed. All access from single-threaded async event loop.
Dict operations are atomic under GIL.
"""

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
from .types import ModelResourceInfo, ModelStatus, SystemResourceInfo

logger = get_logger(__name__)


def _normalize_key(model_id: str | ModelId) -> str:
    """Get normalized string key for dict lookup.

    Normalization: strips -hybrid suffix, preserves -cpu.
    'model-8192-hybrid' → 'model-8192'
    'model-8192-cpu' → 'model-8192-cpu'
    """
    if isinstance(model_id, ModelId):
        return model_id.normalized
    return ModelId.parse(model_id).normalized


class ResourceTracker:
    """Resource tracking for model lifecycle and resource consumption.

    Thread Safety: Not needed. All methods called from single-threaded
    async event loop. Dict operations are atomic under GIL.

    Keys in _models and _state_machines are normalized strings for consistent
    lookups across -hybrid variants.
    """

    def __init__(self, event_bus: Any = None):
        """Initialize the resource tracker."""
        self.logger = get_logger(__name__)
        self._models: dict[str, ModelResourceInfo] = {}
        self._state_machines: dict[str, WorkerStateMachine] = {}
        self._system_info: SystemResourceInfo | None = None
        self._initialized = False
        self.event_bus = event_bus
        self._initialize_system_resources()

    def set_event_bus(self, event_bus: Any) -> None:
        """Set the EventBus for publishing events."""
        self.event_bus = event_bus

    def _initialize_system_resources(self) -> None:
        """Initialize system resource information."""
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
            self.logger.error(f"Failed to initialize resource tracker: {e}")
            self._initialized = False

    # -------------------------------------------------------------------------
    # Model Registration
    # -------------------------------------------------------------------------

    def register_model(self, model_id: str | ModelId) -> None:
        """Register a new model for tracking with state machine.

        Thread Safety: Not needed. All access from single-threaded async event loop.
        GIL ensures dict operations are atomic.
        """
        key = _normalize_key(model_id)
        if key not in self._models:
            model_str = str(model_id) if isinstance(model_id, str) else str(model_id)
            self._models[key] = ModelResourceInfo(model_id=model_str)
            self._state_machines[key] = WorkerStateMachine(
                worker_id=model_str, initial_state=WorkerState.UNINITIALIZED
            )
            self.logger.info(f"Registered model for tracking: {model_id}")

    def unregister_model(self, model_id: str | ModelId) -> None:
        """Unregister a model from tracking."""
        key = _normalize_key(model_id)
        self._models.pop(key, None)
        self._state_machines.pop(key, None)
        self.logger.info(f"Unregistered model from tracking: {model_id}")

    # -------------------------------------------------------------------------
    # Model Status Management
    # -------------------------------------------------------------------------

    def set_model_status(
        self,
        model_id: str | ModelId,
        status: ModelStatus,
        error_message: str | None = None,
    ) -> None:
        """Set the status of a model."""
        key = _normalize_key(model_id)
        if key not in self._models:
            self.register_model(model_id)
        self._models[key].status = status
        self._models[key].error_message = error_message
        self._models[key].last_updated = time.time()
        if status == ModelStatus.LOADED and self._models[key].load_time is None:
            self._models[key].load_time = time.time()
        self.logger.debug(f"Model {model_id} status: {status.value}")

    def set_model_loading(self, model_id: str | ModelId) -> None:
        """Mark a model as loading. Resets ERROR state automatically."""
        model_str = str(model_id) if isinstance(model_id, str) else str(model_id)
        key = _normalize_key(model_id)
        handle_error_state_recovery(self._state_machines, self._models, key, model_str)
        if key not in self._models:
            self.register_model(model_id)
        transition_to_loading(
            self._state_machines, key, model_str, self.set_model_status
        )

    def set_model_loaded(
        self, model_id: str | ModelId, process_pid: int | None = None
    ) -> None:
        """Mark a model as loaded."""
        key = _normalize_key(model_id)
        if key in self._state_machines:
            success = self._state_machines[key].transition(
                WorkerState.LOADED, reason="model_loaded_successfully"
            )
            if success:
                self.set_model_status(model_id, ModelStatus.LOADED)
                if process_pid and key in self._models:
                    self._models[key].process_pid = process_pid
        else:
            self.set_model_status(model_id, ModelStatus.LOADED)
            if process_pid and key in self._models:
                self._models[key].process_pid = process_pid

    async def set_model_busy(self, model_id: str | ModelId) -> None:
        """Mark a model as busy (processing inference).

        Emits INFERENCE_STARTED event to notify Stargate.
        """
        key = _normalize_key(model_id)
        model_str = str(model_id) if isinstance(model_id, str) else str(model_id)
        if key in self._state_machines:
            success = self._state_machines[key].transition(
                WorkerState.BUSY,
                reason="inference_started",
                guard=lambda: self._state_machines[key].current_state
                == WorkerState.LOADED,
            )
            if not success:
                self.logger.warning(
                    f"Cannot mark {model_id} as busy - invalid transition"
                )
                return
        if key in self._models:
            self._models[key].status = ModelStatus.BUSY
            self._models[key].current_inference_start = time.time()
            self._models[key].last_updated = time.time()
            self.logger.debug(f"Model {model_id} marked as busy")
            await emit_inference_started(self.event_bus, model_str)

    async def set_model_idle(self, model_id: str | ModelId) -> None:
        """Mark a model as idle (finished inference or cancelled).

        Emits INFERENCE_COMPLETED event to notify Stargate.
        """
        key = _normalize_key(model_id)
        model_str = str(model_id) if isinstance(model_id, str) else str(model_id)
        transition_to_idle(self._state_machines, key, model_str)
        await update_model_idle_status_async(
            self._models,
            key,
            model_str,
            self.event_bus,
        )

    def set_model_inference_state(
        self, model_id: str | ModelId, inference_state: str
    ) -> None:
        """Set inference state ('token_counting' or 'generating')."""
        key = _normalize_key(model_id)
        if key in self._models:
            if self._models[key].status == ModelStatus.BUSY:
                self._models[key].inference_state = inference_state
                self._models[key].last_updated = time.time()
                self.logger.debug(
                    f"Model {model_id} inference state: {inference_state}"
                )
            else:
                self.logger.warning(
                    f"Cannot set inference state for {model_id} - not busy"
                )

    def set_model_error(self, model_id: str | ModelId, error_message: str) -> None:
        """Mark a model as having an error."""
        key = _normalize_key(model_id)
        if key not in self._models:
            self.register_model(model_id)
        current_info = self._models[key]
        if current_info.status == ModelStatus.ERROR and current_info.error_message:
            self.logger.warning(
                f"Model {model_id} already in ERROR. Ignoring new error: {error_message}"
            )
            return
        if key in self._state_machines:
            self._state_machines[key].set_error(error_message)
        self.set_model_status(model_id, ModelStatus.ERROR, error_message)

    def get_model_error(self, model_id: str | ModelId) -> str | None:
        """Get error message if model has error status."""
        key = _normalize_key(model_id)
        if key in self._models:
            info = self._models[key]
            if info.status == ModelStatus.ERROR:
                return info.error_message
        return None

    def set_model_unloading(self, model_id: str | ModelId) -> None:
        """Mark a model as unloading."""
        self.set_model_status(model_id, ModelStatus.UNLOADING)

    async def force_model_idle(self, model_id: str | ModelId, reason: str) -> bool:
        """Force a model to idle state (for cancellation).

        Emits INFERENCE_COMPLETED event to notify Stargate.
        """
        key = _normalize_key(model_id)
        model_str = str(model_id) if isinstance(model_id, str) else str(model_id)
        sm_success = False
        if key in self._state_machines:
            sm_success = self._state_machines[key].force_idle(reason)
        if key in self._models:
            m = self._models[key]
            m.status = ModelStatus.LOADED
            m.last_inference_end = m.last_inference_time = time.time()
            m.current_inference_start = m.inference_state = None
            m.last_updated = time.time()
            self.logger.info(
                f"✅ Model {model_id} forced idle (reason: {reason}, sm={sm_success})"
            )

            last_inference_time = m.last_inference_end or time.time()
            if m.last_inference_end is None:
                self.logger.warning(
                    f"MODEL_IDLE emitted for {model_id} without "
                    "pre-recorded last_inference_end; falling back to current time"
                )

            await emit_inference_completed(
                self.event_bus, model_str, last_inference_time
            )
            return True
        self.logger.warning(f"⚠️ Cannot force idle for {model_id} - not in tracker")
        return False

    # -------------------------------------------------------------------------
    # Inference Tracking
    # -------------------------------------------------------------------------

    @asynccontextmanager
    async def track_inference(self, model_id: str | ModelId):
        """Context manager to track inference lifecycle."""
        try:
            await self.set_model_busy(model_id)
            self.logger.debug(f"Model {model_id} busy for inference")
            yield
        finally:
            await self.set_model_idle(model_id)
            self.logger.debug(f"Model {model_id} idle after inference")

    # -------------------------------------------------------------------------
    # Resource Updates
    # -------------------------------------------------------------------------

    def update_model_resources(
        self, model_id: str | ModelId, vram_mb: int, ram_mb: int
    ) -> None:
        """Update resource usage for a model."""
        key = _normalize_key(model_id)
        if key not in self._models:
            self.register_model(model_id)
        self._models[key].vram_usage_mb = vram_mb or 0
        self._models[key].ram_usage_mb = ram_mb or 0
        self._models[key].last_updated = time.time()
        self.logger.debug(f"Updated {model_id}: VRAM={vram_mb}MB, RAM={ram_mb}MB")

    def update_model_last_inference_time(
        self, model_id: str | ModelId, ts: float | None = None
    ) -> None:
        """Update the last inference time for a model."""
        key = _normalize_key(model_id)
        if key in self._models:
            self._models[key].last_inference_time = ts or time.time()
            self._models[key].last_updated = time.time()

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
        final_vram = actual_vram if actual_vram is not None else fallback_vram
        final_ram = actual_ram if actual_ram is not None else fallback_ram
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
        """Get state machine by model ID."""
        return self._state_machines.get(_normalize_key(model_id))

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
