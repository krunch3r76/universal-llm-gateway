"""Unit tests for libs/sse/accumulator.py.

Coverage targets (one per Phase 3 §accumulator_design point):
    - Happy path: events delivered, reducer signals terminal, state returned
    - Reducer exception → SSEReductionError wraps original, stream closes
    - Stall: no events for stall_timeout → SSEStallError
    - Overall timeout: wall-clock cap fires → SSETimeoutError
    - Provider error event → reducer.terminal_error called; default raises SSEProviderError
    - Provider error event with absorbing reducer → returns state cleanly
    - Cancel check → returns current state without raising
    - on_event fires per successful reduce; in stream order
    - on_event exception suppressed; stream continues
    - Stream exhaustion before reducer terminal → returns state as-is
    - asyncio.CancelledError from reducer is re-raised (not wrapped)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from sse.accumulator import accumulate_sse_stream
from sse.core import SSEMessage
from sse.protocols import (
    SSEProviderError,
    SSEReductionError,
    SSEStallError,
    SSETimeoutError,
)


@dataclass
class CountingState:
    events_seen: int = 0
    last_data: str = ""
    on_event_calls: list[tuple[str | None, str]] = field(default_factory=list)


class CountingReducer:
    """Simple reducer: count events, terminate on data == 'stop'."""

    def initial_state(self) -> CountingState:
        return CountingState()

    def reduce(self, state: CountingState, event: SSEMessage) -> bool:
        state.events_seen += 1
        state.last_data = event.data if isinstance(event.data, str) else ""
        return state.last_data == "stop"

    def terminal_error(self, state: CountingState, event: SSEMessage) -> None:
        raise SSEProviderError(f"provider error: {event.data}")


class AbsorbingReducer(CountingReducer):
    """Same as CountingReducer but absorbs provider errors into state."""

    def terminal_error(self, state: CountingState, event: SSEMessage) -> None:
        state.last_data = f"absorbed:{event.data}"


async def _aiter(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


async def _slow_aiter(chunks: list[bytes], delay_s: float) -> AsyncIterator[bytes]:
    for chunk in chunks:
        await asyncio.sleep(delay_s)
        yield chunk


@pytest.mark.asyncio
async def test_happy_path_terminates_on_reducer_signal() -> None:
    raw = b"data: a\n\ndata: b\n\ndata: stop\n\ndata: never\n\n"
    state = await accumulate_sse_stream(_aiter(raw), CountingReducer())
    assert state.events_seen == 3
    assert state.last_data == "stop"


@pytest.mark.asyncio
async def test_stream_exhausted_returns_state_as_is() -> None:
    raw = b"data: one\n\ndata: two\n\n"
    state = await accumulate_sse_stream(_aiter(raw), CountingReducer())
    assert state.events_seen == 2
    assert state.last_data == "two"


@pytest.mark.asyncio
async def test_reducer_exception_wrapped_in_reduction_error() -> None:
    class BoomReducer(CountingReducer):
        def reduce(self, state: CountingState, event: SSEMessage) -> bool:
            raise ValueError("boom")

    raw = b"data: hi\n\n"
    with pytest.raises(SSEReductionError) as info:
        await accumulate_sse_stream(_aiter(raw), BoomReducer())
    assert isinstance(info.value.original, ValueError)
    assert info.value.event.data == "hi"


@pytest.mark.asyncio
async def test_cancelled_error_is_reraised_not_wrapped() -> None:
    class CancellingReducer(CountingReducer):
        def reduce(self, state: CountingState, event: SSEMessage) -> bool:
            raise asyncio.CancelledError()

    raw = b"data: hi\n\n"
    with pytest.raises(asyncio.CancelledError):
        await accumulate_sse_stream(_aiter(raw), CancellingReducer())


@pytest.mark.asyncio
async def test_stall_timeout_fires_when_no_events() -> None:
    """Single chunk delayed beyond stall_timeout → SSEStallError."""
    chunks = [b"data: hi\n\n"]
    with pytest.raises(SSEStallError) as info:
        await accumulate_sse_stream(
            _slow_aiter(chunks, delay_s=0.5),
            CountingReducer(),
            stall_timeout=0.1,
        )
    assert info.value.elapsed_seconds >= 0.1


@pytest.mark.asyncio
async def test_overall_timeout_fires_independent_of_stall() -> None:
    """Many quick events but overall budget exceeded → SSETimeoutError."""

    async def trickle() -> AsyncIterator[bytes]:
        for _ in range(100):
            await asyncio.sleep(0.01)
            yield b"data: tick\n\n"

    with pytest.raises(SSETimeoutError) as info:
        await accumulate_sse_stream(
            trickle(),
            CountingReducer(),
            stall_timeout=10.0,
            overall_timeout=0.05,
        )
    assert info.value.elapsed_seconds >= 0.05


@pytest.mark.asyncio
async def test_provider_error_event_raises_default() -> None:
    raw = b"data: ok\n\nevent: error\ndata: rate-limited\n\n"
    with pytest.raises(SSEProviderError) as info:
        await accumulate_sse_stream(_aiter(raw), CountingReducer())
    assert "rate-limited" in str(info.value)


@pytest.mark.asyncio
async def test_provider_error_absorbed_returns_state() -> None:
    raw = b"data: ok\n\nevent: error\ndata: rate-limited\n\n"
    state = await accumulate_sse_stream(_aiter(raw), AbsorbingReducer())
    assert state.events_seen == 1
    assert state.last_data == "absorbed:rate-limited"


@pytest.mark.asyncio
async def test_cancel_check_returns_state_cleanly() -> None:
    raw = b"data: a\n\ndata: b\n\ndata: c\n\n"
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # cancel on second poll

    state = await accumulate_sse_stream(
        _aiter(raw),
        CountingReducer(),
        cancel_check=cancel,
    )
    # First poll allows one event; second poll cancels before the next.
    assert state.events_seen == 1


@pytest.mark.asyncio
async def test_on_event_fires_per_reduce_in_order() -> None:
    raw = b"data: a\n\ndata: b\n\ndata: stop\n\n"
    reducer = CountingReducer()

    def on_event(event: SSEMessage, current: CountingState) -> None:
        current.on_event_calls.append(
            (event.event, event.data if isinstance(event.data, str) else "")
        )

    final = await accumulate_sse_stream(_aiter(raw), reducer, on_event=on_event)
    assert [d for _, d in final.on_event_calls] == ["a", "b", "stop"]


@pytest.mark.asyncio
async def test_on_event_exception_is_suppressed() -> None:
    raw = b"data: a\n\ndata: stop\n\n"

    def boom(event: SSEMessage, current: CountingState) -> None:
        raise RuntimeError("hook should not abort the stream")

    state = await accumulate_sse_stream(_aiter(raw), CountingReducer(), on_event=boom)
    assert state.events_seen == 2
    assert state.last_data == "stop"


@pytest.mark.asyncio
async def test_on_event_does_not_fire_on_provider_error() -> None:
    raw = b"event: error\ndata: bad\n\n"
    fired = {"n": 0}

    def hook(event: SSEMessage, current: CountingState) -> None:
        fired["n"] += 1

    with pytest.raises(SSEProviderError):
        await accumulate_sse_stream(
            _aiter(raw),
            CountingReducer(),
            on_event=hook,
        )
    assert fired["n"] == 0


@pytest.mark.asyncio
async def test_on_event_does_not_fire_on_reducer_failure() -> None:
    raw = b"data: hi\n\n"

    class BoomReducer(CountingReducer):
        def reduce(self, state: CountingState, event: SSEMessage) -> bool:
            raise ValueError("boom")

    fired = {"n": 0}

    def hook(event: SSEMessage, current: CountingState) -> None:
        fired["n"] += 1

    with pytest.raises(SSEReductionError):
        await accumulate_sse_stream(
            _aiter(raw),
            BoomReducer(),
            on_event=hook,
        )
    assert fired["n"] == 0
