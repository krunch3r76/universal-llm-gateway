"""Event-loop and GIL gap instrumentation for RAG health measurement (friction:39359).

Starts from ``lifecycle._startup`` and stops from ``lifecycle._shutdown``. Emits
``loop_drift_ms`` (asyncio schedule lateness) and ``gil_gap_ms`` (daemon thread
wake gaps labelled GIL). Not imported at module load — monitors start only with
the serving process lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_LOOP_SLEEP_S = 0.01
_GIL_GAP_LOG_THRESHOLD_MS = 50.0
# Same floor as the GIL series. Logging every positive drift (~100 Hz) makes
# the pass-bar series measure its own log I/O (friction:39359 M1).
_LOOP_DRIFT_LOG_THRESHOLD_MS = _GIL_GAP_LOG_THRESHOLD_MS

_loop_drift_task: asyncio.Task[None] | None = None
_loop_drift_stop: asyncio.Event | None = None
_gil_thread: threading.Thread | None = None
_gil_stop: threading.Event | None = None


async def _loop_drift_monitor(stop: asyncio.Event) -> None:
    """Log ``loop_drift_ms`` when the 10 ms sleep overshoots by more than 50 ms."""
    loop = asyncio.get_running_loop()
    next_target = loop.time() + _LOOP_SLEEP_S
    while not stop.is_set():
        await asyncio.sleep(_LOOP_SLEEP_S)
        now = loop.time()
        drift_ms = max(0.0, (now - next_target) * 1000.0)
        if drift_ms > _LOOP_DRIFT_LOG_THRESHOLD_MS:
            logger.info("loop_drift_ms=%.3f", drift_ms)
        next_target = now + _LOOP_SLEEP_S


def _gil_gap_monitor(stop: threading.Event) -> None:
    """Log ``gil_gap_ms`` when a 10 ms thread sleep overshoots by more than 50 ms."""
    next_target = time.perf_counter() + _LOOP_SLEEP_S
    while not stop.wait(_LOOP_SLEEP_S):
        now = time.perf_counter()
        gap_ms = (now - next_target) * 1000.0
        if gap_ms > _GIL_GAP_LOG_THRESHOLD_MS:
            logger.info("gil_gap_ms=%.3f GIL", gap_ms)
        next_target = now + _LOOP_SLEEP_S


def start_loop_instrumentation() -> None:
    """Start asyncio loop-drift and GIL-gap monitor threads (idempotent).

    Safe to call once per process; repeated calls are ignored until stop runs.
    """
    global _loop_drift_task, _loop_drift_stop, _gil_thread, _gil_stop
    if _loop_drift_task is not None:
        return
    _loop_drift_stop = asyncio.Event()
    _loop_drift_task = asyncio.create_task(
        _loop_drift_monitor(_loop_drift_stop), name="rag-loop-drift"
    )
    _gil_stop = threading.Event()
    _gil_thread = threading.Thread(
        target=_gil_gap_monitor,
        args=(_gil_stop,),
        name="rag-gil-gap",
        daemon=True,
    )
    _gil_thread.start()


async def stop_loop_instrumentation() -> None:
    """Stop monitors started by ``start_loop_instrumentation``.

    Cancels the asyncio drift task and joins the GIL-gap daemon thread.
    """
    global _loop_drift_task, _loop_drift_stop, _gil_thread, _gil_stop
    if _loop_drift_stop is not None:
        _loop_drift_stop.set()
    if _loop_drift_task is not None:
        await asyncio.gather(_loop_drift_task, return_exceptions=True)
    _loop_drift_task = None
    _loop_drift_stop = None
    if _gil_stop is not None:
        _gil_stop.set()
    if _gil_thread is not None:
        _gil_thread.join(timeout=1.0)
    _gil_thread = None
    _gil_stop = None
