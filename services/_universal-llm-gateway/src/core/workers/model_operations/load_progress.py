"""Load progress heartbeat during active model load."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..controller import WorkerController

LOAD_PROGRESS_CADENCE_S = 15.0

PHASE_PCT: dict[str, int] = {
    "preflight": 5,
    "worker_start": 20,
    "config": 40,
    "engine_wait": 65,
    "finalize": 90,
}


class LoadProgressHeartbeat:
    """Emit model.loading.progress at phase boundaries and <=15s cadence."""

    def __init__(self, controller: WorkerController, model_id: str) -> None:
        self._controller = controller
        self._model_id = model_id
        self._phase = "preflight"
        self._pct = PHASE_PCT["preflight"]
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        from .load_flow import emit_loading_progress

        await emit_loading_progress(
            self._controller, self._model_id, self._phase, self._pct
        )
        self._task = asyncio.create_task(
            self._heartbeat_loop(),
            name=f"load-progress-{self._model_id}",
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def emit_phase(self, phase: str, pct: int | None = None) -> None:
        self._phase = phase
        self._pct = pct if pct is not None else PHASE_PCT.get(phase, self._pct)
        from .load_flow import emit_loading_progress

        await emit_loading_progress(
            self._controller, self._model_id, self._phase, self._pct
        )

    async def _heartbeat_loop(self) -> None:
        from .load_flow import emit_loading_progress

        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=LOAD_PROGRESS_CADENCE_S
                    )
                    return
                except TimeoutError:
                    await emit_loading_progress(
                        self._controller,
                        self._model_id,
                        self._phase,
                        self._pct,
                    )
        except asyncio.CancelledError:
            return
