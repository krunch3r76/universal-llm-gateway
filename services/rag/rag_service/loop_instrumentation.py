"""Event-loop and GIL gap instrumentation for RAG health measurement (friction:39359).

Starts from ``lifecycle._startup`` and stops from ``lifecycle._shutdown``. Emits
``loop_drift_ms`` (asyncio schedule lateness) and ``gil_gap_ms`` (daemon thread
wake gaps labelled GIL). A daemon stack watchdog (friction:39420 r3) dumps the
loop thread when the drift heartbeat is late. Not imported at module load —
monitors start only with the serving process lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import traceback
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
_watchdog_thread: threading.Thread | None = None
_watchdog_stop: threading.Event | None = None
_heartbeat_mono: float = 0.0
_loop_ident: int | None = None
_loop_ref: asyncio.AbstractEventLoop | None = None
_watchdog_cfg: tuple[float, float, float, int, float] = (
    2.0,
    10.0,
    30.0,
    12,
    0.25,
)


def _env_flag_on(name: str) -> bool:
    """True unless the env value is ``0``, ``false``, or ``off``. Unset is on."""
    raw = os.environ.get(name, "1").strip().lower()
    return raw not in {"0", "false", "off"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _touch_heartbeat() -> None:
    global _heartbeat_mono
    _heartbeat_mono = time.monotonic()


def _loop_thread_name(ident: int) -> str:
    main = threading.main_thread()
    if main.ident == ident:
        return main.name
    for thread in threading.enumerate():
        if thread.ident == ident:
            return thread.name
    return str(ident)


def _format_tasks(loop: asyncio.AbstractEventLoop | None) -> str:
    if loop is None:
        return "unavailable"
    try:
        tasks = list(asyncio.all_tasks(loop))
    except RuntimeError:
        return "unavailable"
    if not tasks:
        return "none"
    parts: list[str] = []
    for task in tasks[:20]:
        coro = task.get_coro()
        qual = getattr(coro, "__qualname__", type(coro).__name__)
        parts.append(f"{task.get_name()}:{qual}")
    return ",".join(parts)


def _format_frames(ident: int | None, limit: int) -> str:
    if ident is None:
        return "unavailable"
    frame = sys._current_frames().get(ident)
    if frame is None:
        return "unavailable"
    lines = traceback.format_stack(frame, limit=limit)
    collapsed = [" ".join(line.split()) for line in lines]
    return " || ".join(collapsed)


def _emit_stack_dump(min_interval_s: float, frame_limit: int) -> None:
    age_s = time.monotonic() - _heartbeat_mono
    late_ms = max(0.0, age_s) * 1000.0
    thread_name = (
        _loop_thread_name(_loop_ident) if _loop_ident is not None else "unknown"
    )
    logger.warning(
        "loop_stack_dump late_ms=%.0f thread=%s interval_s=%.0f tasks=%s frames=%s",
        late_ms,
        thread_name,
        min_interval_s,
        _format_tasks(_loop_ref),
        _format_frames(_loop_ident, frame_limit),
    )


def _stack_watchdog(stop: threading.Event) -> None:
    """Dump the loop thread when its heartbeat is older than ``late_s``.

    A task cannot run during the stall this thread exists to see, so task
    names come from ``asyncio.all_tasks`` on the watchdog thread.
    """
    late_s, long_late_s, min_interval_s, frame_limit, poll_s = _watchdog_cfg
    last_dump = 0.0
    armed = True
    long_sample_done = False
    while not stop.wait(poll_s):
        age_s = time.monotonic() - _heartbeat_mono
        now = time.monotonic()
        if age_s > late_s:
            if armed and (now - last_dump) >= min_interval_s:
                _emit_stack_dump(min_interval_s, frame_limit)
                last_dump = time.monotonic()
                armed = False
            elif not long_sample_done and age_s > long_late_s:
                _emit_stack_dump(min_interval_s, frame_limit)
                last_dump = time.monotonic()
                long_sample_done = True
        else:
            long_sample_done = False
            if (now - last_dump) >= min_interval_s:
                armed = True


async def _loop_drift_monitor(stop: asyncio.Event) -> None:
    """Log ``loop_drift_ms`` when the 10 ms sleep overshoots by more than 50 ms."""
    loop = asyncio.get_running_loop()
    _touch_heartbeat()
    next_target = loop.time() + _LOOP_SLEEP_S
    while not stop.is_set():
        await asyncio.sleep(_LOOP_SLEEP_S)
        _touch_heartbeat()
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
    """Start asyncio loop-drift, GIL-gap, and stack-watchdog monitors (idempotent).

    Safe to call once per process; repeated calls are ignored until stop runs.
    ``RAG_LOOP_STACK_WATCHDOG=0|false|off`` skips the watchdog thread. Other
    stack env (    ``RAG_LOOP_STACK_LATE_S``, ``RAG_LOOP_STACK_LONG_S``, ``RAG_LOOP_STACK_FRAMES``,
    ``RAG_LOOP_STACK_MIN_INTERVAL_S``, ``RAG_LOOP_STACK_POLL_S``) is read once
    here.
    """
    global _loop_drift_task, _loop_drift_stop, _gil_thread, _gil_stop
    global _watchdog_thread, _watchdog_stop, _loop_ident, _loop_ref, _watchdog_cfg
    if _loop_drift_task is not None:
        return
    loop = asyncio.get_running_loop()
    _loop_ref = loop
    _loop_ident = threading.get_ident()
    _touch_heartbeat()
    _watchdog_cfg = (
        _env_float("RAG_LOOP_STACK_LATE_S", 2.0),
        _env_float("RAG_LOOP_STACK_LONG_S", 10.0),
        _env_float("RAG_LOOP_STACK_MIN_INTERVAL_S", 30.0),
        _env_int("RAG_LOOP_STACK_FRAMES", 12),
        _env_float("RAG_LOOP_STACK_POLL_S", 0.25),
    )
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
    if _env_flag_on("RAG_LOOP_STACK_WATCHDOG"):
        _watchdog_stop = threading.Event()
        _watchdog_thread = threading.Thread(
            target=_stack_watchdog,
            args=(_watchdog_stop,),
            name="rag-loop-stack-watchdog",
            daemon=True,
        )
        _watchdog_thread.start()


async def stop_loop_instrumentation() -> None:
    """Stop monitors started by ``start_loop_instrumentation``.

    Cancels the asyncio drift task and joins the GIL-gap and stack-watchdog
    daemon threads (1 s each).
    """
    global _loop_drift_task, _loop_drift_stop, _gil_thread, _gil_stop
    global _watchdog_thread, _watchdog_stop, _loop_ident, _loop_ref
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
    if _watchdog_stop is not None:
        _watchdog_stop.set()
    if _watchdog_thread is not None:
        _watchdog_thread.join(timeout=1.0)
    _watchdog_thread = None
    _watchdog_stop = None
    _loop_ident = None
    _loop_ref = None
