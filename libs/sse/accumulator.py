"""SSE stream accumulator — drives a reducer over an iter_sse_events source.

Public entrypoint:
    accumulate_sse_stream(byte_iter, reducer, *, on_event=None,
                          stall_timeout=90.0, overall_timeout=None,
                          cancel_check=None) -> State

Liveness:
    stall_timeout: per-event inactivity cap. Resets on every SSEMessage
        delivered by iter_sse_events (including comments, pings, thinking
        deltas — anything that hits the wire). Default 90s.
    overall_timeout: wall-clock cap from stream open to terminal state.
        Default None (no cap; outer pipeline executor handles long-tail).

Termination:
    reducer.reduce() returns True → clean termination, returns state.
    iter_sse_events exhausted before reducer signals terminal → returns state
        as-is (caller's responsibility to know whether that's an error).
    SSE error event (event.event == "error") → reducer.terminal_error(state, event)
        is called; default raises SSEProviderError.
    reducer.reduce() raises → wrapped in SSEReductionError.
    iter_sse_events raises SSEParseError → propagated unchanged.
    stall_timeout exceeded → SSEStallError.
    overall_timeout exceeded → SSETimeoutError.
    cancel_check returns True between events → returns state as-is (clean stop).

Observability:
    on_event(event, state) fires AFTER reducer.reduce() per event. Sync only.
    Exceptions inside on_event are caught, logged via universal_logging, and
    silently dropped — they do NOT abort the stream and do NOT raise
    SSEReductionError. Callers must NOT call back into the accumulator,
    block on I/O, or start nested dispatches from within on_event.
    on_event does NOT fire on error paths (parse failure, provider error event,
    reducer exception, stall, timeout).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from typing import cast

from universal_logging import get_logger

from sse.core import SSEMessage
from sse.framing import iter_sse_events
from sse.protocols import (
    SSEReducer,
    SSEReductionError,
    SSEStallError,
    SSETimeoutError,
)

logger = get_logger(__name__)

DEFAULT_STALL_TIMEOUT_S = 90.0


async def accumulate_sse_stream[State](
    byte_iter: AsyncIterator[bytes],
    reducer: SSEReducer[State],
    *,
    on_event: Callable[[SSEMessage, State], None] | None = None,
    stall_timeout: float = DEFAULT_STALL_TIMEOUT_S,
    overall_timeout: float | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> State:
    """Drive a reducer over an SSE byte stream until terminal state.

    Args:
        byte_iter: Async iterator producing SSE bytes (typically
            ``httpx.Response.aiter_raw()``).
        reducer: The SSEReducer implementation. Owns the State type.
        on_event: Optional sync callback fired after every successful reduce.
            See module docstring for the execution contract.
        stall_timeout: Max seconds between events. Default 90.0.
        overall_timeout: Max seconds for the whole stream. Default None.
        cancel_check: Optional sync predicate polled between events. Returning
            True ends the stream cleanly with the current state.

    Returns:
        The reducer's terminal State.

    Raises:
        SSEStallError: No event arrived within ``stall_timeout`` seconds.
        SSETimeoutError: Wall-clock exceeded ``overall_timeout`` seconds.
        SSEReductionError: ``reducer.reduce()`` raised; original wrapped.
        SSEProviderError: Provider emitted ``event: error`` and
            ``reducer.terminal_error()`` raised (default behaviour).
        SSEParseError: Malformed bytes/frame from ``iter_sse_events``.
    """
    state = reducer.initial_state()
    stream = iter_sse_events(byte_iter)
    started_at = time.monotonic()
    last_event_at = started_at

    try:
        while True:
            if cancel_check is not None and cancel_check():
                return state

            now = time.monotonic()
            if overall_timeout is not None and (now - started_at) >= overall_timeout:
                raise SSETimeoutError(now - started_at)

            # Per-event wait with stall_timeout. Compute the inner wait so it
            # never exceeds the remaining overall budget (so SSETimeoutError
            # fires before SSEStallError when both are within reach).
            wait_s = stall_timeout
            if overall_timeout is not None:
                remaining = overall_timeout - (now - started_at)
                wait_s = min(wait_s, max(0.0, remaining))

            try:
                event = await asyncio.wait_for(_anext(stream), timeout=wait_s)
            except TimeoutError as exc:
                stall_elapsed = time.monotonic() - last_event_at
                # If the overall budget closed first, prefer that error.
                if (
                    overall_timeout is not None
                    and (time.monotonic() - started_at) >= overall_timeout
                ):
                    raise SSETimeoutError(time.monotonic() - started_at) from exc
                raise SSEStallError(stall_elapsed) from exc
            except StopAsyncIteration:
                # Stream exhausted before reducer signalled terminal.
                return state

            last_event_at = time.monotonic()

            # Provider-level error event → reducer.terminal_error() decides.
            if event.event == "error":
                reducer.terminal_error(state, event)
                # If terminal_error absorbed the error (didn't raise), treat as
                # clean termination with current state.
                return state

            try:
                is_terminal = reducer.reduce(state, event)
            except BaseException as exc:
                # Wrap and propagate. Catching BaseException so reducer cannot
                # smuggle a SystemExit/KeyboardInterrupt past the caller — but
                # CancelledError is re-raised verbatim per asyncio contract.
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise SSEReductionError(exc, event) from exc

            if on_event is not None:
                _fire_on_event(on_event, event, state)

            if is_terminal:
                return state
    finally:
        # iter_sse_events is an async generator — close it explicitly so that
        # underlying httpx response cleanup runs even on early termination.
        await stream.aclose()


async def _anext(stream: AsyncIterator[SSEMessage]) -> SSEMessage:
    """Helper so asyncio.wait_for has a coroutine-returning callable.

    Re-raises StopAsyncIteration as-is so the caller can detect end-of-stream
    distinctly from a stall timeout.
    """
    return await cast("AsyncIterator[SSEMessage]", stream).__anext__()


def _fire_on_event(
    callback: Callable[[SSEMessage, object], None],
    event: SSEMessage,
    state: object,
) -> None:
    """Invoke the on_event callback with full exception isolation.

    Per the Phase 3 contract:
        - synchronous (no await)
        - exceptions caught, logged, dropped — never propagate
        - exceptions do NOT raise SSEReductionError

    Logs at WARNING level; if observability becomes structurally important
    later, switch to a debug event emission via emit_debug_event from
    universal_event_bus.events.debug — but the no-propagate semantics stay.
    """
    try:
        callback(event, state)
    except Exception:
        logger.warning(
            "on_event callback raised; suppressing per contract",
            exc_info=True,
        )
