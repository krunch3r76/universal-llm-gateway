"""Unit tests for GoogleStreamReducer — basic streaming and thought handling.

Coverage:
- Single-chunk text response (degenerate fixture)
- Multi-chunk text-delta concatenation
- Thought parts split from regular text at thought-boundary transitions
- usageMetadata last-frame-wins

Tool-call/terminal/error tests: ``test_google_reducer_tools.py``
Fixture integration tests: ``test_google_reducer_fixtures.py``
"""

from __future__ import annotations

import json
from typing import Any

from sse.core import SSEMessage

from llm_adapters.streaming.google import GoogleStreamReducer, _GoogleState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _frame(payload: dict[str, Any]) -> SSEMessage:
    """Wrap a GenerateContentResponse payload as an SSE data frame."""
    return SSEMessage(data=json.dumps(payload), event=None)


def _drive(events: list[SSEMessage]) -> _GoogleState:
    r = GoogleStreamReducer()
    state = r.initial_state()
    for evt in events:
        done = r.reduce(state, evt)
        if done:
            break
    return state


# ---------------------------------------------------------------------------
# Unit: reduce() always returns False (no terminal event in Gemini SSE)
# ---------------------------------------------------------------------------


def test_reduce_never_returns_true_even_with_finish_reason() -> None:
    r = GoogleStreamReducer()
    state = r.initial_state()
    evt = _frame(
        {
            "candidates": [
                {
                    "content": {"parts": [{"text": "done"}], "role": "model"},
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 1},
            "modelVersion": "gemini-2.5-flash",
        }
    )
    assert r.reduce(state, evt) is False
    # finish_reason must still be captured for the terminal dict.
    assert state.finish_reason == "STOP"


def test_reduce_ignores_empty_payload() -> None:
    r = GoogleStreamReducer()
    state = r.initial_state()
    assert r.reduce(state, _frame({})) is False
    assert state.parts == []
    assert state.usage_metadata == {}


# ---------------------------------------------------------------------------
# Unit: text-delta concatenation across frames
# ---------------------------------------------------------------------------


def test_text_deltas_concatenate_across_frames() -> None:
    state = _drive(
        [
            _frame(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": "Hello"}],
                                "role": "model",
                            },
                            "index": 0,
                        }
                    ],
                    "modelVersion": "gemini-2.5-flash",
                }
            ),
            _frame(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": " world"}],
                                "role": "model",
                            },
                            "index": 0,
                        }
                    ],
                }
            ),
            _frame(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": "!"}],
                                "role": "model",
                            },
                            "finishReason": "STOP",
                            "index": 0,
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 3,
                        "candidatesTokenCount": 3,
                    },
                }
            ),
        ]
    )
    result = GoogleStreamReducer.to_terminal_dict(state)
    parts = result["candidates"][0]["content"]["parts"]
    assert len(parts) == 1, "three text deltas must merge into one part"
    assert parts[0]["text"] == "Hello world!"
    assert result["candidates"][0]["finishReason"] == "STOP"
    assert result["usageMetadata"]["candidatesTokenCount"] == 3


def test_usage_metadata_last_frame_wins() -> None:
    state = _drive(
        [
            _frame(
                {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "a"}], "role": "model"},
                            "index": 0,
                        }
                    ],
                    "usageMetadata": {"candidatesTokenCount": 1, "totalTokenCount": 10},
                }
            ),
            _frame(
                {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "b"}], "role": "model"},
                            "index": 0,
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"candidatesTokenCount": 2, "totalTokenCount": 20},
                }
            ),
        ]
    )
    result = GoogleStreamReducer.to_terminal_dict(state)
    assert result["usageMetadata"]["candidatesTokenCount"] == 2
    assert result["usageMetadata"]["totalTokenCount"] == 20


# ---------------------------------------------------------------------------
# Unit: thought boundary splits text runs
# ---------------------------------------------------------------------------


def test_thought_text_separate_from_regular_text() -> None:
    """A thought=true text run and a plain text run must be kept distinct."""
    state = _drive(
        [
            _frame(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": "thinking...", "thought": True}],
                                "role": "model",
                            },
                            "index": 0,
                        }
                    ],
                }
            ),
            _frame(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": "more thinking", "thought": True}],
                                "role": "model",
                            },
                            "index": 0,
                        }
                    ],
                }
            ),
            _frame(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": "The answer is 42."}],
                                "role": "model",
                            },
                            "finishReason": "STOP",
                            "index": 0,
                        }
                    ],
                }
            ),
        ]
    )
    parts = GoogleStreamReducer.to_terminal_dict(state)["candidates"][0]["content"][
        "parts"
    ]
    assert len(parts) == 2
    # Thought run merges into a single thought-text part.
    assert parts[0].get("thought") is True
    assert parts[0]["text"] == "thinking...more thinking"
    # Plain text run is kept separate — no thought flag.
    assert "thought" not in parts[1]
    assert parts[1]["text"] == "The answer is 42."
