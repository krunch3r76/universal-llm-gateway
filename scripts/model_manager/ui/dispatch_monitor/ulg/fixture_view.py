"""Replay JSONL fixtures through :class:`MonitorController`'s in-process hub.

Same derive-then-publish shape as :meth:`MonitorController.tick`, but folds fixture
records synchronously and never calls ``tick()`` (live Event Service backfill) or
``seed()`` (cold-start history).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from scripts.model_manager.ui.dispatch_monitor.core.dtos import SupervisorProjection
from scripts.model_manager.ui.dispatch_monitor.core.protocols import EventRecord
from scripts.model_manager.ui.dispatch_monitor.core.replay import JsonlEventSource
from scripts.model_manager.ui.dispatch_monitor.ulg.controller import MonitorController

PaintCallback = Callable[[SupervisorProjection], None]


def subscribe_paint(controller: MonitorController, paint: PaintCallback) -> None:
    """Register ``paint`` on the controller hub (e.g. ``list.append`` in tests)."""
    controller.hub.subscribe(paint)


def derive_and_publish(controller: MonitorController, now_ms: int) -> bool:
    """Derive once at ``now_ms``; publish when the fingerprint changed."""
    frame = controller.model.derive(now_ms, previous=controller._last_frame)
    if frame.fingerprint == controller._last_fingerprint:
        controller._last_frame = frame
        return False
    controller._last_fingerprint = frame.fingerprint
    controller._last_frame = frame
    controller.hub.publish(frame)
    return True


def replay_records(
    controller: MonitorController,
    records: Iterable[EventRecord],
    *,
    now_ms: int,
) -> tuple[int, int]:
    """Apply each record, derive after each apply, publish on fingerprint change.

    Returns ``(records_applied, frames_published)``.
    """
    applied = 0
    published = 0
    for record in records:
        controller.model.apply(record)
        applied += 1
        if derive_and_publish(controller, now_ms):
            published += 1
    return applied, published


def replay_fixture_path(
    controller: MonitorController,
    path: str,
    *,
    now_ms: int | None = None,
) -> tuple[int, int]:
    """Load JSONL from ``path`` and replay through ``controller``."""
    source = JsonlEventSource.from_path(path)
    clock = source.max_ts() if now_ms is None else now_ms
    return replay_records(controller, source.records, now_ms=clock)


def run_fixture_replay(
    path: str,
    paint: PaintCallback,
    *,
    controller: MonitorController | None = None,
    now_ms: int | None = None,
) -> tuple[int, int]:
    """Convenience entry: hub + paint, replay one fixture file."""
    ctrl = controller or MonitorController()
    subscribe_paint(ctrl, paint)
    return replay_fixture_path(ctrl, path, now_ms=now_ms)
