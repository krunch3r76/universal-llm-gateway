"""SSE accumulator protocols and exceptions.

Independent of the accumulator driver (`accumulator.py`) — type-checkers and
reducers can import from this module without pulling the asyncio loop.

Contains:
    SSEReducer[State]    — mutable-accumulator protocol for stream reducers
    SSEStreamStats       — dataclass populated by the accumulator (timing, counts)
    SSEError             — base exception
    SSEParseError        — malformed bytes/frame in iter_sse_events
    SSEReductionError    — exception inside reducer.reduce(); wraps original
    SSEStallError        — no event within stall_timeout
    SSETimeoutError      — overall wall-clock exceeded
    SSEProviderError     — stream-level `event: error` from the provider
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from sse.core import SSEMessage


class SSEError(Exception):
    """Base for all SSE accumulator errors."""


class SSEParseError(SSEError):
    """Malformed bytes or frame inside iter_sse_events.

    Raised before any reducer.reduce() is called, so reducer state is undefined.
    """


class SSEReductionError(SSEError):
    """Reducer.reduce() raised. Wraps the original exception.

    Attributes:
        original: The exception raised by reducer.reduce().
        event: The SSEMessage that triggered the failure.
    """

    def __init__(self, original: BaseException, event: SSEMessage) -> None:
        super().__init__(f"reducer raised {type(original).__name__}: {original}")
        self.original = original
        self.event = event


class SSEStallError(SSEError):
    """No SSE event arrived within stall_timeout.

    Attributes:
        elapsed_seconds: Wall-clock seconds since the last event arrival.
    """

    def __init__(self, elapsed_seconds: float) -> None:
        super().__init__(f"SSE stream stalled for {elapsed_seconds:.2f}s")
        self.elapsed_seconds = elapsed_seconds


class SSETimeoutError(SSEError):
    """Overall wall-clock cap exceeded.

    Attributes:
        elapsed_seconds: Wall-clock seconds since stream open.
    """

    def __init__(self, elapsed_seconds: float) -> None:
        super().__init__(
            f"SSE stream exceeded overall_timeout after {elapsed_seconds:.2f}s"
        )
        self.elapsed_seconds = elapsed_seconds


class SSEProviderError(SSEError):
    """Provider emitted a stream-level `event: error` frame.

    Distinct from SSEReductionError (reducer-side failure) and SSEParseError
    (malformed bytes). Raised from the reducer's terminal_error() unless that
    method absorbs the error into state.
    """


@dataclass
class SSEStreamStats:
    """Per-stream timing and counts. Populated by accumulator; reducers that
    care about it attach it to terminal state via convention (e.g.
    `state["_streaming_metrics"] = stats`).
    """

    event_count: int = 0
    first_event_at: float = 0.0
    last_event_at: float = 0.0
    terminal_at: float = 0.0
    stall_periods: list[float] = field(default_factory=list)


class SSEReducer[State](Protocol):
    """Mutable-accumulator protocol for SSE event reducers.

    The reducer owns the State type (returned by initial_state) and mutates
    it in-place per event. This matches the real-world LLM-stream pattern
    where State carries many accumulating fields (text deltas, tool-call
    arrays, thinking traces, usage tallies). Pure-functional alternatives
    require either deep-copy-per-event (memory blow-up) or dict-aliasing
    discipline that the protocol cannot enforce.

    Implementations:
        - libs/llm_adapters/streaming/anthropic.py (Phase: out-of-scope step 5)
        - libs/llm_adapters/streaming/openai.py    (out-of-scope step 8)
        - libs/llm_adapters/streaming/google.py    (out-of-scope step 8)

    Methods:
        initial_state: Return the starting state. Called once per stream.
        reduce: Mutate state in-place using event. Return True to terminate
            the stream cleanly (e.g. on `event: message_stop`). Raising any
            exception aborts the stream with SSEReductionError.
        terminal_error: Called when the provider emits a stream-level error
            event (event.event == "error"). Default behaviour (per Q8): raise
            SSEProviderError. Override to absorb the error into state for
            partial-result recovery.
    """

    def initial_state(self) -> State: ...

    def reduce(self, state: State, event: SSEMessage) -> bool:
        """Mutate state with event. Return True if this event terminates the stream."""
        ...

    def terminal_error(self, state: State, event: SSEMessage) -> None:
        """Handle provider-emitted error event. Default: raise SSEProviderError."""
        ...
