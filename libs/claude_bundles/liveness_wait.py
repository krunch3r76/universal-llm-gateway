"""Inspect-window wait for claude.ai dump and mirror.

Dump navigation, composer readiness, and zip harvest call ``liveness_wait``
instead of a Playwright deadline. The window is an inspect period: expiry
never means failure. Callers inject a progress probe, a completion check, and
an error check. Monotonic progress resets a full fresh window. A window with
no progress raises ``LivenessStalledError``. An explicit error raises
``LivenessError`` immediately, without waiting for expiry.

``CLAUDE_DUMP_LIVENESS_WINDOW_S`` sizes that inspect window (default 30). It
is not a cap on how long a moving dump may run.
"""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable

_DEFAULT_WINDOW_S = 30.0
_ENV_WINDOW = "CLAUDE_DUMP_LIVENESS_WINDOW_S"


class LivenessStalledError(RuntimeError):
    """No monotonic progress across one full inspect window.

    Raised by ``liveness_wait`` when the progress signature does not advance
    between arm and expiry. Callers print ``STALLED <phase>`` and stop. This
    is not a timer-expiry failure and not an explicit page error.
    """

    def __init__(self, message: str, *, phase: str) -> None:
        super().__init__(message)
        self.phase = phase


class LivenessError(RuntimeError):
    """An explicit error event arrived while a liveness wait was armed.

    Distinct from ``LivenessStalledError``: the probe reported a failure (navigation
    error, download exception) and the wait ends immediately, labelled as an
    error. Callers print ``MIRROR_ERROR`` and do not keep resetting the window.
    """

    def __init__(self, message: str, *, phase: str) -> None:
        super().__init__(message)
        self.phase = phase


def liveness_window_s() -> float:
    """Return the inspect-window length in seconds.

    Reads ``CLAUDE_DUMP_LIVENESS_WINDOW_S``. A missing or non-numeric value
    falls back to 30. The result is always positive. Expiry of this window
    triggers an inspection, not a failure.
    """
    raw = os.environ.get(_ENV_WINDOW, "").strip()
    if not raw:
        return _DEFAULT_WINDOW_S
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_WINDOW_S
    if value <= 0:
        return _DEFAULT_WINDOW_S
    return value


def signature_progressed(previous: object, current: object) -> bool:
    """Return whether ``current`` is monotonic progress beyond ``previous``.

    Numbers advance only when they grow. Equal-length tuples advance when any
    numeric component grows or any non-numeric component changes. A smaller
    number alone is not life: a wedged socket must not reset the window.
    """
    if isinstance(previous, bool) or isinstance(current, bool):
        return previous != current
    if isinstance(previous, (int, float)) and isinstance(current, (int, float)):
        return current > previous
    if (
        isinstance(previous, tuple)
        and isinstance(current, tuple)
        and len(previous) == len(current)
    ):
        advanced = False
        for old, new in zip(previous, current):
            if isinstance(old, bool) or isinstance(new, bool):
                if old != new:
                    advanced = True
            elif isinstance(old, (int, float)) and isinstance(new, (int, float)):
                if new > old:
                    advanced = True
            elif old != new:
                advanced = True
        return advanced
    return previous != current


def _error_text(err: BaseException | str) -> str:
    if isinstance(err, BaseException):
        return f"{type(err).__name__}: {err}"
    return str(err)


async def liveness_wait(
    *,
    progress: Callable[[], Awaitable[object]],
    complete: Callable[[], Awaitable[bool]],
    error: Callable[[], Awaitable[BaseException | str | None]] | None = None,
    window_s: float | None = None,
    poll_s: float = 0.25,
    phase: str = "wait",
    on_progress: Callable[[object], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> None:
    """Arm an inspect window and wait until completion, stall, or error.

    ``progress`` returns a signature. On expiry the signature is sampled
    again; advancement resets ``window_s`` from scratch (a full fresh window).
    No advancement raises ``LivenessStalledError``. ``error`` is polled every
    ``poll_s`` and raises ``LivenessError`` immediately, before the next
    expiry. ``complete`` ends the wait successfully.

    Prints ``PHASE <phase> start|progress|done`` on stdout (line-flushed).
    Pass ``on_progress`` to replace the progress line. ``clock`` and ``sleep``
    are injectable so tests can expire windows without waiting wall time.
    """
    import asyncio

    async def _sleep(seconds: float) -> None:
        if seconds <= 0:
            return
        if sleep is not None:
            await sleep(seconds)
        else:
            await asyncio.sleep(seconds)

    async def _error() -> BaseException | str | None:
        if error is None:
            return None
        return await error()

    window = window_s if window_s is not None else liveness_window_s()
    if window <= 0:
        raise ValueError("liveness window must be positive")
    print(f"PHASE {phase} start", flush=True)
    ticks = 0

    def _note(sig: object) -> None:
        nonlocal ticks
        if on_progress is not None:
            on_progress(sig)
            return
        ticks += 1
        print(f"PHASE {phase} progress n={ticks}", flush=True)

    err = await _error()
    if err:
        raise LivenessError(_error_text(err), phase=phase)
    baseline = await progress()
    deadline = clock() + window
    while True:
        err = await _error()
        if err:
            raise LivenessError(_error_text(err), phase=phase)
        if await complete():
            print(f"PHASE {phase} done", flush=True)
            return
        now = clock()
        if now >= deadline:
            current = await progress()
            if signature_progressed(baseline, current):
                baseline = current
                deadline = clock() + window
                _note(current)
                continue
            raise LivenessStalledError(
                f"no progress across {window:g}s window in phase {phase}",
                phase=phase,
            )
        await _sleep(min(poll_s, deadline - now))
