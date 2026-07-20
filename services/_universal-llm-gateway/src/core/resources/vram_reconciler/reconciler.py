"""VramReconciler class composing periodic VRAM reconciliation concern mixins."""

from __future__ import annotations

import asyncio

from .ghost_sweep import GhostSweepMixin
from .gpu_process import GpuProcessMixin
from .loop import ReconcileLoopMixin
from .phantom_cleanup import PhantomCleanupMixin
from .protocols import EventBusProto, ResourceTrackerProto, WorkerControllerProto
from .tracker_state import TrackerStateMixin
from .vram_discrepancy import VramDiscrepancyMixin


class VramReconciler(
    ReconcileLoopMixin,
    GhostSweepMixin,
    PhantomCleanupMixin,
    VramDiscrepancyMixin,
    GpuProcessMixin,
    TrackerStateMixin,
):
    """Detect and clean up phantom and ghost GPU workers.

    Runs every RECONCILE_INTERVAL_S and performs three sweeps:
    1. Phantom sweep: running process ∉ tracked → force-unload orphan.
    2. Ghost sweep: tracked model whose engine is dead → unload + emit MODEL_UNLOADED.
    3. VRAM discrepancy: |hardware − catalog| > threshold → alert.
    """

    def __init__(
        self,
        resource_tracker: ResourceTrackerProto,
        worker_controller: WorkerControllerProto,
        event_bus: EventBusProto | None = None,
    ) -> None:
        self._resource_tracker: ResourceTrackerProto = resource_tracker
        self._worker_controller: WorkerControllerProto = worker_controller
        self._event_bus: EventBusProto | None = event_bus
        self._task: asyncio.Task[None] | None = None
