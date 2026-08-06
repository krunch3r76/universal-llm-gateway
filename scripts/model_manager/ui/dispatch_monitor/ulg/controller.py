"""Live Controller — seed, subscribe, clock tick, derive, publish."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from scripts.model_manager.ui.dispatch_monitor.core.codec import ProjectionCodec
from scripts.model_manager.ui.dispatch_monitor.core.dtos import (
    SupervisorProjection,
    Thresholds,
)
from scripts.model_manager.ui.dispatch_monitor.core.model import Model, hints_after_drop
from scripts.model_manager.ui.dispatch_monitor.core.protocols import EventRecord
from scripts.model_manager.ui.dispatch_monitor.core.watch import render
from scripts.model_manager.ui.dispatch_monitor.ulg.connection_watermarks import (
    ConnectionWatermarks,
    family_key_for_signal,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.projection_hub import BroadcastHub
from scripts.model_manager.ui.dispatch_monitor.ulg.reconcile_on_click import (
    ReconcileOnClick,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.seeder import seed_model
from scripts.model_manager.ui.dispatch_monitor.ulg.subscribe_session import (
    run_live_subscribers,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.terminal_backfill import (
    backfill_sdk_fold,
    lease_released_without_terminal_ids,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.transport_events import (
    fold_status_transport_event,
    replay_truncated_event,
)


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
        reconcile: ReconcileOnClick | None = None,
    ) -> None:
        self.model = Model(thresholds or Thresholds())
        self.clock = LiveClock()
        self.tick_s = tick_s
        self.seed_minutes = seed_minutes
        self.hub = BroadcastHub()
        self.watermarks = ConnectionWatermarks.fresh()
        self._reconcile = reconcile
        self._last_fingerprint: str | None = None
        self._last_frame = None
        self._apply_lock = asyncio.Lock()
        self._reseed_lock = asyncio.Lock()
        self._pending_drop_hint = False

    def _apply(self, record: EventRecord) -> None:
        self.model.apply(record)
        family = family_key_for_signal(record.signal)
        if family is not None:
            self.watermarks.advance(family, record.seq)

    async def _apply_async(self, record: EventRecord) -> None:
        async with self._apply_lock:
            self._apply(record)

    def seed(self) -> int:
        seeded = seed_model(
            self._apply,
            minutes=self.seed_minutes,
            sdk_fold=self.model.sdk,
            backfill_minutes=max(self.seed_minutes, 24 * 60),
        )
        return seeded

    def _backfill_live_terminals(self) -> int:
        """G4b: clear completed-present LIVE rows from ES when fold missed apply."""
        return backfill_sdk_fold(
            self._apply,
            self.model.sdk,
            minutes=max(self.seed_minutes, 24 * 60),
        )

    def tick(self) -> bool:
        """Derive once; publish when fingerprint changes. Returns whether a frame emitted."""
        if lease_released_without_terminal_ids(self.model.sdk):
            self._backfill_live_terminals()
        frame = self.model.derive(self.clock.now_ms(), previous=self._last_frame)
        if self._pending_drop_hint:
            frame = hints_after_drop(frame)
            self._pending_drop_hint = False
        if frame.fingerprint == self._last_fingerprint:
            self._last_frame = frame
            return False
        self._last_fingerprint = frame.fingerprint
        self._last_frame = frame
        self.hub.publish(frame)
        return True

    def mark_subscriber_drop(self) -> None:
        """F4 hook — stamp ``("*",)`` on the next delivered frame after a hub drop."""
        self._pending_drop_hint = True

    async def _handle_truncation(
        self,
        connection: str,
        requested_seq: int | None,
        detail: dict[str, Any],
    ) -> None:
        ts = self.clock.now_ms()
        reason = str(detail.get("reason") or "truncated")
        first_seq = detail.get("first_seq")
        first = first_seq if isinstance(first_seq, int) and not isinstance(first_seq, bool) else None
        async with self._apply_lock:
            self._apply(
                replay_truncated_event(
                    connection=connection,
                    requested_seq=requested_seq,
                    reason=reason,
                    first_seq=first,
                    ts_unix_ms=ts,
                )
            )
            self._apply(
                fold_status_transport_event(
                    fold_status="reseeding",
                    reason=reason,
                    connection=connection,
                    ts_unix_ms=ts,
                )
            )
        self.tick()
        await self._reseed_after_truncation(connection)

    async def _reseed_after_truncation(self, connection: str) -> None:
        async with self._reseed_lock:
            thresholds = self.model.thresholds
            async with self._apply_lock:
                self.model = Model(thresholds)
                self.model.replay_truncations.clear()
                self._last_fingerprint = None
                self._last_frame = None
                self.watermarks = ConnectionWatermarks.fresh()
                seeded = seed_model(
                    self._apply,
                    minutes=self.seed_minutes,
                    sdk_fold=self.model.sdk,
                    backfill_minutes=max(self.seed_minutes, 24 * 60),
                )
                ts = self.clock.now_ms()
                self._apply(
                    fold_status_transport_event(
                        fold_status="live",
                        reason="reseed_complete",
                        connection=connection,
                        ts_unix_ms=ts,
                    )
                )
            self.tick()
            if seeded == 0:
                async with self._apply_lock:
                    self._apply(
                        fold_status_transport_event(
                            fold_status="suspect",
                            reason="reseed_empty",
                            connection=connection,
                            ts_unix_ms=self.clock.now_ms(),
                        )
                    )
                self.tick()

    def trigger_reconcile(self, subject: str) -> dict[str, object]:
        """Explicit operator reconcile for one subject ref; never called from tick."""
        ts = self.clock.now_ms()
        if self._reconcile is None:
            from scripts.model_manager.ui.dispatch_monitor.ulg.reconcile_events import (
                source_failure_event,
            )

            self._apply(
                source_failure_event(
                    subject=subject,
                    source="port",
                    error="reconcile_unwired",
                    ts_unix_ms=ts,
                )
            )
            self.tick()
            return {"subject": subject, "error": "reconcile_unwired", "applied": 1}

        events, outcomes = self._reconcile.reconcile_subject(subject)
        for event in events:
            self._apply(event)
        self.tick()
        return {
            "subject": subject,
            "applied": len(events),
            "sources": [
                {"source": item.source, "ok": item.ok, "error": item.error}
                for item in outcomes
            ],
        }

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
        on_projection: Callable[[SupervisorProjection], None] | None = None,
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

        def hub_sink(frame: SupervisorProjection) -> None:
            if on_projection is not None:
                on_projection(frame)
            if json_frames:
                emit(ProjectionCodec.encode_snapshot(frame))
            elif on_frame is not None:
                emit(render(frame))

        self.hub.subscribe(hub_sink)
        stop = asyncio.Event()
        clock_task = asyncio.create_task(self._clock_loop(stop))
        try:
            await run_live_subscribers(
                self._apply_async,
                watermarks=self.watermarks,
                on_truncated=self._handle_truncation,
            )
        finally:
            stop.set()
            await clock_task
