"""Unit tests for libs/sse/protocols.py.

Lightweight coverage — these are dataclasses/exceptions/protocols. Verifies:
    - Exception construction stores the documented attributes
    - SSEStreamStats default factory works correctly (no shared list aliasing)
    - SSEReducer protocol is satisfied structurally (typing-time check via runtime construction)
"""

from __future__ import annotations

import pytest

from sse.core import SSEMessage
from sse.protocols import (
    SSEError,
    SSEParseError,
    SSEProviderError,
    SSEReducer,
    SSEReductionError,
    SSEStallError,
    SSEStreamStats,
    SSETimeoutError,
)


def test_sse_error_hierarchy() -> None:
    """All concrete errors derive from SSEError."""
    for cls in (
        SSEParseError,
        SSEReductionError,
        SSEStallError,
        SSETimeoutError,
        SSEProviderError,
    ):
        assert issubclass(cls, SSEError)


def test_reduction_error_carries_original_and_event() -> None:
    msg = SSEMessage(data="x", event="content_block_delta")
    original = ValueError("inner failure")
    err = SSEReductionError(original, msg)
    assert err.original is original
    assert err.event is msg
    assert "inner failure" in str(err)


def test_stall_error_carries_elapsed() -> None:
    err = SSEStallError(91.5)
    assert err.elapsed_seconds == pytest.approx(91.5)
    assert "91.50" in str(err)


def test_timeout_error_carries_elapsed() -> None:
    err = SSETimeoutError(3601.0)
    assert err.elapsed_seconds == pytest.approx(3601.0)


def test_stream_stats_default_factory_isolated() -> None:
    """stall_periods uses field(default_factory=list); two instances must not share."""
    a = SSEStreamStats()
    b = SSEStreamStats()
    a.stall_periods.append(10.0)
    assert b.stall_periods == []


def test_reducer_protocol_structural_satisfaction() -> None:
    """A class implementing the three required methods satisfies the Protocol structurally."""

    class CountingReducer:
        def initial_state(self) -> dict[str, int]:
            return {"count": 0}

        def reduce(self, state: dict[str, int], event: SSEMessage) -> bool:
            state["count"] += 1
            return event.event == "stop"

        def terminal_error(self, state: dict[str, int], event: SSEMessage) -> None:
            raise SSEProviderError(f"provider error: {event.data}")

    # Protocol is structural — assignment compatibility is the test.
    reducer: SSEReducer[dict[str, int]] = CountingReducer()
    state = reducer.initial_state()
    assert reducer.reduce(state, SSEMessage(data="x", event="delta")) is False
    assert reducer.reduce(state, SSEMessage(data="x", event="stop")) is True
    assert state["count"] == 2
