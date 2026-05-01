"""Tool-call, grounding, terminal-state, and error tests for GoogleStreamReducer.

Separated from ``test_google_reducer.py`` to keep both files under the SLOC
ceiling.  Covers:

- functionCall is atomic (not merged with text runs)
- functionCall closes an open text run
- Grounding metadata preserved from last frame
- to_terminal_dict on empty state
- SSEProviderError on stream-level error events
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sse.core import SSEMessage
from sse.protocols import SSEProviderError

from llm_adapters.streaming.google import GoogleStreamReducer, _GoogleState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _frame(payload: dict[str, Any]) -> SSEMessage:
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
# Unit: functionCall is atomic; never merged with neighboring text parts
# ---------------------------------------------------------------------------


def test_function_call_is_atomic() -> None:
    state = _drive(
        [
            _frame(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "functionCall": {
                                            "name": "get_weather",
                                            "args": {"location": "Paris"},
                                        }
                                    }
                                ],
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
    assert len(parts) == 1
    assert "functionCall" in parts[0]
    assert parts[0]["functionCall"]["name"] == "get_weather"
    assert parts[0]["functionCall"]["args"] == {"location": "Paris"}


def test_function_call_closes_text_run() -> None:
    """Text delta before a functionCall must not absorb the functionCall part."""
    state = _drive(
        [
            _frame(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": "Let me check. "}],
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
                                "parts": [
                                    {
                                        "functionCall": {
                                            "name": "lookup",
                                            "args": {},
                                        }
                                    }
                                ],
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
    assert parts[0]["text"] == "Let me check. "
    assert "functionCall" in parts[1]


# ---------------------------------------------------------------------------
# Unit: grounding metadata captured when present
# ---------------------------------------------------------------------------


def test_grounding_metadata_preserved() -> None:
    state = _drive(
        [
            _frame(
                {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "x"}], "role": "model"},
                            "groundingMetadata": {"webSearchQueries": ["q1"]},
                            "finishReason": "STOP",
                            "index": 0,
                        }
                    ],
                }
            ),
        ]
    )
    result = GoogleStreamReducer.to_terminal_dict(state)
    assert result["candidates"][0]["groundingMetadata"] == {
        "webSearchQueries": ["q1"]
    }


# ---------------------------------------------------------------------------
# Unit: empty state returns a benign empty response (no crash)
# ---------------------------------------------------------------------------


def test_to_terminal_dict_empty_state_is_well_formed() -> None:
    result = GoogleStreamReducer.to_terminal_dict(GoogleStreamReducer().initial_state())
    assert result.get("candidates") == []


# ---------------------------------------------------------------------------
# Unit: terminal_error raises SSEProviderError
# ---------------------------------------------------------------------------


def test_terminal_error_raises_provider_error() -> None:
    r = GoogleStreamReducer()
    state = r.initial_state()
    err = SSEMessage(
        data='{"error": {"code": 429, "message": "quota"}}', event="error"
    )
    with pytest.raises(SSEProviderError, match="Google stream error"):
        r.terminal_error(state, err)


def test_terminal_error_handles_malformed_data() -> None:
    r = GoogleStreamReducer()
    state = r.initial_state()
    err = SSEMessage(data="not-json", event="error")
    with pytest.raises(SSEProviderError):
        r.terminal_error(state, err)
