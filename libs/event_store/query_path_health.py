"""Query-path progress signals for /health — distinct from process-up liveness."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

_lock = threading.Lock()
_last_completed_monotonic: float | None = None
_last_duration_s: float | None = None
_event_loop_lag_ms: float | None = None


def record_query_completed(duration_s: float) -> None:
    """Record the wall time of the most recently finished store read."""
    global _last_completed_monotonic, _last_duration_s
    now = time.monotonic()
    with _lock:
        _last_completed_monotonic = now
        _last_duration_s = duration_s


def set_event_loop_lag_ms(lag_ms: float) -> None:
    """Update smoothed event-loop scheduling lag (milliseconds)."""
    global _event_loop_lag_ms
    with _lock:
        _event_loop_lag_ms = lag_ms


def snapshot() -> dict[str, Any]:
    """Return query-path fields suitable for /health and operator triage."""
    with _lock:
        if _last_completed_monotonic is None:
            age_ms: float | None = None
        else:
            age_ms = (time.monotonic() - _last_completed_monotonic) * 1000.0
        return {
            "query_completed_age_ms": age_ms,
            "last_query_duration_s": _last_duration_s,
            "event_loop_lag_ms": _event_loop_lag_ms,
        }


async def run_event_loop_lag_probe(*, interval_s: float = 1.0) -> None:
    """Background task: measure how far asyncio sleep slips under load."""
    loop = asyncio.get_running_loop()
    while True:
        start = loop.time()
        await asyncio.sleep(interval_s)
        lag_ms = max(0.0, (loop.time() - start - interval_s) * 1000.0)
        set_event_loop_lag_ms(lag_ms)
