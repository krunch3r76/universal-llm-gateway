"""ResourceTracker mixin — model load paths, busy/idle, and error surfaces."""

from __future__ import annotations

import time

from model_id import ModelId

from src.core.workers.state_machine import WorkerState

from .events import emit_inference_completed, emit_inference_started
from .hardware import get_process_gpu_memory
from .tracker_keys import _process_key, _tracking_key
from .transitions import (
    handle_error_state_recovery,
    transition_to_idle,
    transition_to_loading,
    update_model_idle_status_async,
)
from .types import ModelStatus


class _ResourceTrackerMixinModelStatus:
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

        Multi-capacity variants (catalog_capacity > 1) may have multiple
        inferences in flight simultaneously. Lifecycle is tracked by the
        WorkerStateMachine (LOADED ↔ BUSY); concurrency is tracked by a
        per-variant counter. The SM transitions LOADED → BUSY on the first
        concurrent inference and stays BUSY until the counter drops to 0.

        Emits INFERENCE_STARTED for every caller (each represents a distinct
        request). Raises RuntimeError if the variant SM is not in
        {LOADED, BUSY} — e.g. UNLOADING, ERROR, LOADING — so track_inference
        can abort.
        """
        tkey = _tracking_key(model_id)
        model_str = str(model_id)
        if tkey in self._state_machines:
            sm = self._state_machines[tkey]
            state = sm.current_state
            if state == WorkerState.LOADED:
                success = sm.transition(
                    WorkerState.BUSY,
                    reason="inference_started",
                    guard=lambda: sm.current_state == WorkerState.LOADED,
                )
                if not success:
                    current = sm.current_state.value
                    msg = (
                        f"Cannot mark {model_id} as busy — variant SM in "
                        f"{current}, expected LOADED"
                    )
                    self.logger.warning(msg)
                    raise RuntimeError(msg)
            elif state != WorkerState.BUSY:
                # UNLOADING, UNLOADED, ERROR, LOADING, UNINITIALIZED — caller
                # must abort; no concurrent-busy slot exists in these states.
                msg = (
                    f"Cannot mark {model_id} as busy — variant SM in "
                    f"{state.value}, expected LOADED or BUSY"
                )
                self.logger.warning(msg)
                raise RuntimeError(msg)
            self._busy_count[tkey] = self._busy_count.get(tkey, 0) + 1
        if tkey in self._models:
            m = self._models[tkey]
            if m.current_inference_start is None:
                m.current_inference_start = time.time()
            self.logger.debug(
                "Model %s marked as busy (concurrent=%d)",
                model_id,
                self._busy_count.get(tkey, 0),
            )
            await emit_inference_started(self.event_bus, model_str, request_id)

    async def set_model_idle(self, model_id: str | ModelId) -> None:
        """Mark one inference on a variant as complete.

        Decrements the per-variant busy counter. The SM transitions
        BUSY → LOADED only when the counter reaches 0, so concurrent
        inferences on a multi-capacity variant don't trip each other's
        guards. Per-request INFERENCE_COMPLETED is emitted on every call.
        """
        tkey = _tracking_key(model_id)
        model_str = str(model_id)
        new_count = max(self._busy_count.get(tkey, 1) - 1, 0)
        self._busy_count[tkey] = new_count
        if new_count > 0:
            # Other inferences still in flight on this variant — only emit
            # the per-request completion event; do not touch SM or clear
            # the model-level current_inference_start field.
            if tkey in self._models:
                now = time.time()
                self._models[tkey].last_inference_end = now
                self._models[tkey].last_inference_time = now
            if self.event_bus is not None:
                await emit_inference_completed(self.event_bus, model_str, time.time())
            return
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
