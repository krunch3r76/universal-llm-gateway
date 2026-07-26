"""Live Controller — seed, subscribe, clock tick, derive, publish."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from scripts.model_manager.ui.dispatch_monitor.core.codec import ProjectionCodec
from scripts.model_manager.ui.dispatch_monitor.core.dtos import Thresholds
from scripts.model_manager.ui.dispatch_monitor.core.model import Model
from scripts.model_manager.ui.dispatch_monitor.core.protocols import EventRecord
from scripts.model_manager.ui.dispatch_monitor.core.watch import render
from scripts.model_manager.ui.dispatch_monitor.ulg.live_source import run_live_subscribers
from scripts.model_manager.ui.dispatch_monitor.ulg.projection_hub import BroadcastHub
from scripts.model_manager.ui.dispatch_monitor.ulg.seeder import seed_model


class LiveClock:
    def now_ms(self) -> int:
        return int(time.time() * 1000)


class MonitorController:
    """G5 Controller: owns I/O, never derives business rules."""

    def __init__(
        self,
        *,
        thresholds: Thresholds | None = None,
        tick_s: float = 1.0,
        seed_minutes: int = 60,
    ) -> None:
        self.model = Model(thresholds or Thresholds())
        self.clock = LiveClock()
        self.tick_s = tick_s
        self.seed_minutes = seed_minutes
        self.hub = BroadcastHub()
        self._last_fingerprint: str | None = None
        self._apply_lock = asyncio.Lock()

    def _apply(self, record: EventRecord) -> None:
        self.model.apply(record)

    async def _apply_async(self, record: EventRecord) -> None:
        async with self._apply_lock:
            self._apply(record)

    def seed(self) -> int:
        return seed_model(self._apply, minutes=self.seed_minutes)

    def tick(self) -> bool:
        """Derive once; publish when fingerprint changes. Returns whether a frame emitted."""
        frame = self.model.derive(self.clock.now_ms())
        if frame.fingerprint == self._last_fingerprint:
            return False
        self._last_fingerprint = frame.fingerprint
        self.hub.publish(frame)
        return True

    async def _clock_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self.tick()
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.tick_s)
            except TimeoutError:
                continue

    async def run(
        self,
        *,
        on_frame: Callable[[str], None] | None = None,
        json_frames: bool = False,
        command_endpoint: str | None = None,
    ) -> None:
        """Seed history, then subscribe live while ticking the clock."""
        seeded = self.seed()
        if on_frame is not None:
            on_frame(f"# seeded {seeded} events")

        if json_frames and on_frame is not None:
            on_frame(ProjectionCodec.encode_handshake(command_endpoint))

        def emit(frame_text: str) -> None:
            if on_frame is not None:
                on_frame(frame_text)

        def hub_sink(frame) -> None:  # noqa: ANN001
            if json_frames:
                emit(ProjectionCodec.encode_snapshot(frame))
            else:
                emit(render(frame))

        self.hub.subscribe(hub_sink)
        stop = asyncio.Event()
        clock_task = asyncio.create_task(self._clock_loop(stop))
        try:
            await run_live_subscribers(self._apply_async)
        finally:
            stop.set()
            await clock_task
