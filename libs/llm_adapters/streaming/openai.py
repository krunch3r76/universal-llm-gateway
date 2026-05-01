"""OpenAI / xAI Responses API SSE reducer.

Both providers share the same Responses API SSE event format:

    event: response.created          — initial metadata (ignored by reducer)
    event: response.in_progress      — status update (ignored by reducer)
    event: response.output_item.added — new output item (message / function_call / reasoning)
    event: response.content_part.added — new content part (ignored by reducer)
    event: response.output_text.delta — text token delta (ignored by reducer)
    event: response.output_text.done  — text part complete (ignored by reducer)
    event: response.content_part.done — content part complete (ignored by reducer)
    event: response.output_item.done  — output item finalized (ignored by reducer)
    event: response.completed         — TERMINAL: full assembled response

Strategy: the ``response.completed`` event carries the fully-assembled response
object (``payload["response"]``).  This is exactly the shape that
``libs/llm_adapters/responses.py:ResponsesAdapter.parse_frontier_response``
expects (``response_data.get("output")``, ``response_data.get("usage")``).
Intermediate delta events are discarded — the terminal event is the single
source of truth for the assembled response.

This mirrors the design of ``AnthropicReducer``, which similarly captures the
final assembled state and returns it as a provider-native dict.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sse.protocols import SSEMessage, SSEProviderError


@dataclass
class _OpenAIResponsesState:
    """Mutable reducer state for a single OpenAI / xAI Responses API stream."""

    terminal_response: dict[str, Any] = field(default_factory=dict)
    # Tracks function-call items as they arrive via response.output_item.added
    # so the on_event hook can report tool names before the stream completes.
    # key: output_index, value: {"name": str, "type": "function_call"}
    pending_items: dict[int, dict[str, Any]] = field(default_factory=dict)


class OpenAIResponsesReducer:
    """Reduce OpenAI / xAI Responses API SSE events to the native response shape.

    The terminal ``response.completed`` event carries the fully-assembled
    ``response`` object.  All intermediate delta events are discarded.
    ``to_terminal_dict`` returns the saved response object, which is exactly
    the shape ``ResponsesAdapter.parse_frontier_response`` expects.
    """

    DEFAULT_STALL_TIMEOUT: float = 60.0

    def initial_state(self) -> _OpenAIResponsesState:
        return _OpenAIResponsesState()

    def reduce(self, state: _OpenAIResponsesState, event: SSEMessage) -> bool:
        """Mutate state.  Return True when the stream is complete."""
        payload = _parse_payload(event)
        event_type = str(payload.get("type") or "")

        if event_type == "response.output_item.added":
            # Track incoming output items so the on_event hook can report
            # function_call tool names before response.completed fires.
            item = payload.get("item")
            output_index = payload.get("output_index")
            if isinstance(item, dict) and isinstance(output_index, int):
                state.pending_items[output_index] = item
            return False

        if event_type == "response.completed":
            response = payload.get("response")
            if isinstance(response, dict):
                state.terminal_response = response
            return True

        return False

    def terminal_error(self, state: _OpenAIResponsesState, event: SSEMessage) -> None:
        _ = state
        try:
            payload = _parse_payload(event)
        except Exception:
            payload = {"raw": event.data}
        raise SSEProviderError(f"OpenAI Responses stream error: {payload}")

    @staticmethod
    def to_terminal_dict(state: _OpenAIResponsesState) -> dict[str, Any]:
        """Return the native Responses API response object for parse_frontier_response."""
        return state.terminal_response


def _parse_payload(event: SSEMessage) -> dict[str, Any]:
    if isinstance(event.data, dict):
        return event.data
    try:
        loaded = json.loads(event.data)
    except (json.JSONDecodeError, TypeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}
