"""Resource tracking data types.

Defines core data structures for model resource tracking:
- ModelStatus: Enum for model lifecycle states (API serialization)
- ModelResourceInfo: Per-model resource and state tracking
- SystemResourceInfo: System-wide resource snapshot

ModelResourceInfo.status is a derived property — its value comes from the
co-located WorkerStateMachine, ensuring status and SM state can never diverge.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.workers.state_machine import WorkerState, WorkerStateMachine


class ModelStatus(Enum):
    """Model status enumeration for lifecycle tracking.

    Kept as the public API enum for serialization compatibility.
    Derived from WorkerState via worker_to_model_status() mapping.
    """

    NOT_LOADED = "not_loaded"
    LOADING = "loading"
    LOADED = "loaded"
    BUSY = "busy"
    UNLOADING = "unloading"
    ERROR = "error"


@lru_cache(maxsize=1)
def worker_to_model_status() -> dict[WorkerState, ModelStatus]:
    from src.core.workers.state_machine import WorkerState

    return {
        WorkerState.UNINITIALIZED: ModelStatus.NOT_LOADED,
        WorkerState.LOADING: ModelStatus.LOADING,
        WorkerState.LOADED: ModelStatus.LOADED,
        WorkerState.BUSY: ModelStatus.BUSY,
        WorkerState.ERROR: ModelStatus.ERROR,
        WorkerState.UNLOADING: ModelStatus.UNLOADING,
        WorkerState.UNLOADED: ModelStatus.NOT_LOADED,
    }


def _status_for_worker_state(state: WorkerState) -> ModelStatus:
    return worker_to_model_status()[state]


@dataclass
class ModelResourceInfo:
    """Resource information for a specific model.

    Tracks resource usage (VRAM/RAM) and operational state (inference timing,
    process info, errors). The status property is derived from the co-located
    WorkerStateMachine — all status changes happen through SM transitions.

    VRAM accounting:
    - vram_usage_mb: catalog estimate at load decision time (pre-load)
    - measured_vram_mb: hardware delta from pynvml (post-load); None if not yet measured
    - effective_vram_mb: measured when available, else catalog estimate
    """

    model_id: str
    inference_state: str | None = None
    vram_usage_mb: int = 0
    measured_vram_mb: int | None = None
    ram_usage_mb: int = 0
    current_inference_start: float | None = None
    last_inference_end: float | None = None
    load_time: float | None = None
    last_inference_time: float | None = None
    error_message: str | None = None
    process_pid: int | None = None
    last_updated: float = field(default_factory=time.time)
    _sm: WorkerStateMachine | None = field(default=None, init=False, repr=False)

    @property
    def status(self) -> ModelStatus:
        """Derived from the co-located WorkerStateMachine.

        Returns NOT_LOADED when no SM is attached (pre-registration or test).
        """
        if self._sm is None:
            return ModelStatus.NOT_LOADED
        return _status_for_worker_state(self._sm.current_state)

    @property
    def effective_vram_mb(self) -> int:
        """Hardware-measured VRAM when available, else catalog estimate.

        Falls back only when measured_vram_mb is None (not yet measured).
        A measured value of 0 is preserved as a valid measurement.
        """
        return (
            self.measured_vram_mb
            if self.measured_vram_mb is not None
            else self.vram_usage_mb
        )


@dataclass
class SystemResourceInfo:
    """System-wide resource information snapshot.

    Captures total and available VRAM/RAM at a point in time.
    """

    total_vram_mb: int = 0
    available_vram_mb: int = 0
    total_ram_mb: int = 0
    available_ram_mb: int = 0
    timestamp: float = field(default_factory=time.time)
