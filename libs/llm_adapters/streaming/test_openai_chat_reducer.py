"""Unit tests for OpenAIChatCompletionsReducer."""

from __future__ import annotations

import json
from typing import Any

from sse.core import SSEMessage

from llm_adapters.streaming.openai_chat import (
    OpenAIChatCompletionsReducer,
    OpenAIChatCompletionsState,
)


def _data(payload: dict[str, Any] | str) -> SSEMessage:
    if isinstance(payload, str):
        return SSEMessage(data=payload)
    return SSEMessage(data=json.dumps(payload))


def _drive(events: list[SSEMessage]) -> OpenAIChatCompletionsState:
    reducer = OpenAIChatCompletionsReducer()
    state = reducer.initial_state()
    for evt in events:
        if reducer.reduce(state, evt):
            break
    return state


def test_accumulates_delta_content_and_done() -> None:
    state = _drive(
        [
            _data(
                {
                    "id": "chatcmpl-abc",
                    "object": "chat.completion.chunk",
                    "model": "hermes-3-local",
                    "choices": [{"index": 0, "delta": {"content": "Hel"}}],
                }
            ),
            _data(
                {
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"content": "lo"}}],
                }
            ),
            _data(
                {
                    "object": "chat.completion.chunk",
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "stop"},
                    ],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 2,
                        "total_tokens": 5,
                    },
                }
            ),
            _data("[DONE]"),
        ]
    )
    assert "".join(state.content_parts) == "Hello"
    assert state.finish_reason == "stop"
    assert state.model == "hermes-3-local"
    assert state.completion_id == "chatcmpl-abc"
    assert state.usage["total_tokens"] == 5
    assert state.saw_done is True
    assert state.delta_content_events == 2
    assert state.sse_payload_events == 3
    assert state.full_message_events == 0
    assert state.chunk_objects == ["chat.completion.chunk"]

    body = OpenAIChatCompletionsReducer.to_chat_completion(state)
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "Hello"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["prompt_tokens"] == 3

    obs = OpenAIChatCompletionsReducer.observability_headers(state)
    assert obs["X-ULG-Pseudostream-Delta-Parts"] == "2"
    assert obs["X-ULG-Pseudostream-Chunk-Objects"] == "chat.completion.chunk"
    assert obs["X-ULG-Pseudostream-Saw-Done"] == "1"


def test_full_message_wrap_counts_separately() -> None:
    """One-shot JSON wrapped as SSE would show full_message_events, not deltas."""
    state = _drive(
        [
            _data(
                {
                    "id": "chatcmpl-oneshot",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "Hello"},
                            "finish_reason": "stop",
                        }
                    ],
                }
            ),
            _data("[DONE]"),
        ]
    )
    assert "".join(state.content_parts) == "Hello"
    assert state.delta_content_events == 0
    assert state.full_message_events == 1
    assert state.chunk_objects == ["chat.completion"]


def test_inline_error_terminates_with_error_on_body() -> None:
    state = _drive(
        [
            _data(
                {
                    "error": {
                        "message": "empty stream",
                        "type": "gateway_error",
                        "code": "empty_stream",
                    }
                }
            ),
            _data("[DONE]"),
        ]
    )
    assert state.error is not None
    assert state.error["code"] == "empty_stream"
    body = OpenAIChatCompletionsReducer.to_chat_completion(state, model="local-x")
    assert body["error"]["code"] == "empty_stream"
    assert body["choices"][0]["message"]["content"] == ""


def test_to_chat_completion_fills_model_fallback() -> None:
    state = OpenAIChatCompletionsState(content_parts=["ok"])
    body = OpenAIChatCompletionsReducer.to_chat_completion(state, model="fallback-id")
    assert body["model"] == "fallback-id"
    assert body["id"].startswith("chatcmpl-pseudo-")
