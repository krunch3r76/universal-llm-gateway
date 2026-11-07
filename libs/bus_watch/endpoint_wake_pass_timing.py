"""Per-pass timing for endpoint_wake_dispatcher; safe observation emit."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_EMIT_FAILURE_LOG_INTERVAL_S = 60.0
_emit_failure_logged_at: float | None = None

PASS_PHASES: tuple[str, ...] = (
    "normalize_legacy_pending_wakes",
    "escalate_composer_deferred_on_sweep",
    "expire_overdue_hops",
    "scan_once",
    "fire_due_wakes",
    "finalize_composer_defer_pass",
    "check_cursor_ide_context_hops",
    "run_patrol_pass",
)


@dataclass
class _PassTimingState:
    pass_started: float = 0.0
    phase_starts: dict[str, float] = field(default_factory=dict)
    phase_ms: dict[str, float] = field(default_factory=dict)
    display_post_count: int = 0
    display_post_ms: float = 0.0
    fire_due_wakes_iterations: int = 0


_current: _PassTimingState | None = None


def reset_pass_timing_for_tests() -> None:
    """Clear in-process pass timing state (test isolation)."""
    global _current, _emit_failure_logged_at
    _current = None
    _emit_failure_logged_at = None


def begin_pass() -> None:
    """Mark the start of one dispatcher outer pass."""
    global _current
    _current = _PassTimingState(pass_started=time.monotonic())


def begin_phase(phase: str) -> None:
    if _current is None:
        return
    _current.phase_starts[phase] = time.monotonic()


def end_phase(phase: str) -> None:
    if _current is None:
        return
    started = _current.phase_starts.pop(phase, None)
    if started is None:
        return
    elapsed_ms = (time.monotonic() - started) * 1000.0
    _current.phase_ms[phase] = _current.phase_ms.get(phase, 0.0) + elapsed_ms


def note_fire_due_wakes_iteration() -> None:
    if _current is not None:
        _current.fire_due_wakes_iterations += 1


def note_display_post_duration(duration_ms: float) -> None:
    if _current is not None:
        _current.display_post_count += 1
        _current.display_post_ms += duration_ms


def build_pass_duration_payload() -> dict[str, Any] | None:
    """Snapshot timing fields for one pass; None if no pass is active."""
    if _current is None:
        return None
    total_ms = (time.monotonic() - _current.pass_started) * 1000.0
    payload: dict[str, Any] = {
        "total_pass_ms": round(total_ms, 3),
        "display_post_count": _current.display_post_count,
        "display_post_cumulative_ms": round(_current.display_post_ms, 3),
        "fire_due_wakes_iteration_count": _current.fire_due_wakes_iterations,
    }
    for phase in PASS_PHASES:
        key = f"{phase}_ms"
        payload[key] = round(_current.phase_ms.get(phase, 0.0), 3)
    return payload


def safe_emit_pass_duration() -> None:
    """Emit one pass-duration observation; never raises into the dispatcher loop."""
    global _emit_failure_logged_at
    payload = build_pass_duration_payload()
    if payload is None:
        return
    try:
        from bus_watch.events import emit_endpoint_wake_pass_duration

        emit_endpoint_wake_pass_duration(**payload)
    except Exception:
        now = time.monotonic()
        if (
            _emit_failure_logged_at is None
            or (now - _emit_failure_logged_at) >= _EMIT_FAILURE_LOG_INTERVAL_S
        ):
            _emit_failure_logged_at = now
            logger.exception("endpoint_wake_dispatcher: pass_duration emit failed")
