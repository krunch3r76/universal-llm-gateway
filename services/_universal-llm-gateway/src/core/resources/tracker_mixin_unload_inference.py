"""ResourceTracker mixin — unload, force-idle, and inference context manager."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from model_id import ModelId

from src.core.workers.state_machine import WorkerState

from .events import emit_inference_completed
from .tracker_keys import _process_key, _tracking_key


class _ResourceTrackerMixinUnloadAndInference:
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
            self._busy_count[vkey] = 0

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
            self._busy_count[vkey] = 0

    async def force_model_idle(self, model_id: str | ModelId, reason: str) -> bool:
        """Force a model to idle state (for cancellation).

        Forces SM to LOADED and zeroes the concurrent-inference counter —
        this is a "force everything to idle" semantic used for error/cancel
        recovery, not a per-request decrement.
        """
        tkey = _tracking_key(model_id)
        model_str = str(model_id)
        sm_success = False
        if tkey in self._state_machines:
            sm_success = self._state_machines[tkey].force_idle(reason)
        self._busy_count[tkey] = 0
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

        Only invokes set_model_idle when set_model_busy actually succeeded —
        otherwise an entry-time failure would decrement the counter belonging
        to other concurrent inferences on the same multi-capacity variant.
        """
        t0 = time.monotonic()
        self.logger.info(
            f"⏱️ track_inference ENTER: model={model_id} request={request_id}"
        )
        marked_busy = False
        try:
            await self.set_model_busy(model_id, request_id)
            marked_busy = True
            yield
        finally:
            elapsed_ms = (time.monotonic() - t0) * 1000
            self.logger.info(
                f"⏱️ track_inference EXIT: model={model_id} request={request_id} "
                f"held={elapsed_ms:.0f}ms"
            )
            if marked_busy:
                await self.set_model_idle(model_id)
