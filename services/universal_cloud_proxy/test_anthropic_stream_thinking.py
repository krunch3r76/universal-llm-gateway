"""B2b regression tests — streaming thinking deltas.

Verifies the Anthropic SSE → OpenAI chunk translator accumulates
``thinking`` content-block deltas and emits them on stream close as a
``reasoning_content`` field mirroring the non-streaming shape produced by
``convert_response_content``.
"""

from __future__ import annotations

import json
from typing import Any

from services.universal_cloud_proxy.adapters.anthropic_response import (
    convert_response_content,
)
from services.universal_cloud_proxy.adapters.anthropic_stream import StreamTranslator


def _sse_events(events: list[tuple[str, dict[str, Any]]]) -> list[str]:
    """Flatten (event_type, data_dict) pairs into SSE lines."""
    lines: list[str] = []
    for event_type, data in events:
        lines.append(f"event: {event_type}")
        lines.append(f"data: {json.dumps(data)}")
        lines.append("")
    return lines


def _feed(translator: StreamTranslator, lines: list[str]) -> list[dict[str, Any]]:
    """Feed SSE lines through the translator, returning parsed delta dicts.

    Returns the ``choices[0].delta`` dict from each non-``[DONE]`` frame
    emitted. Does not include the final ``[DONE]`` marker.
    """
    deltas: list[dict[str, Any]] = []
    for line in lines:
        for frame in translator.process_line(line):
            raw = frame.decode()
            if raw.strip() == "data: [DONE]":
                continue
            assert raw.startswith("data: ")
            payload = json.loads(raw[len("data: ") :].strip())
            deltas.append(payload["choices"][0]["delta"])
    for frame in translator.finalize():
        raw = frame.decode()
        if raw.strip() == "data: [DONE]":
            continue
        payload = json.loads(raw[len("data: ") :].strip())
        deltas.append(payload["choices"][0]["delta"])
    return deltas


def _thinking_stream(
    parts: list[str],
    signature: str | None = "sig",
    index: int = 0,
) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = [
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "thinking"},
            },
        ),
    ]
    for part in parts:
        events.append(
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "thinking_delta", "thinking": part},
                },
            )
        )
    if signature is not None:
        events.append(
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "signature_delta", "signature": signature},
                },
            )
        )
    events.append(
        ("content_block_stop", {"type": "content_block_stop", "index": index}),
    )
    return events


def _text_stream(text: str, index: int = 1) -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": index}),
    ]


def test_streaming_thinking_matches_non_streaming_shape() -> None:
    events = (
        [("message_start", {"type": "message_start", "message": {"id": "msg_1"}})]
        + _thinking_stream(["Let me ", "consider…"], signature="abc", index=0)
        + _text_stream("The answer is 42.", index=1)
        + [("message_stop", {"type": "message_stop"})]
    )
    translator = StreamTranslator(model_id="claude-opus-4.5")
    deltas = _feed(translator, _sse_events(events))

    reasoning_deltas = [d for d in deltas if "reasoning_content" in d]
    assert len(reasoning_deltas) == 1
    streamed_reasoning = reasoning_deltas[0]["reasoning_content"]

    non_streaming_message, _, _, _ = convert_response_content(
        [
            {
                "type": "thinking",
                "thinking": "Let me consider…",
                "signature": "abc",
            },
            {"type": "text", "text": "The answer is 42."},
        ]
    )
    assert streamed_reasoning == non_streaming_message["reasoning_content"]
    assert translator.reasoning_content == streamed_reasoning


def test_streaming_no_thinking_omits_reasoning_field() -> None:
    events = (
        [("message_start", {"type": "message_start", "message": {"id": "msg_1"}})]
        + _text_stream("Just text.", index=0)
        + [("message_stop", {"type": "message_stop"})]
    )
    translator = StreamTranslator(model_id="claude-opus-4.5")
    deltas = _feed(translator, _sse_events(events))

    assert not any("reasoning_content" in d for d in deltas)
    assert translator.reasoning_content == []


def test_streaming_empty_thinking_is_filtered() -> None:
    events = (
        [("message_start", {"type": "message_start", "message": {"id": "msg_1"}})]
        + _thinking_stream([], signature=None, index=0)
        + _text_stream("Answer.", index=1)
        + [("message_stop", {"type": "message_stop"})]
    )
    translator = StreamTranslator(model_id="claude-opus-4.5")
    deltas = _feed(translator, _sse_events(events))
    assert not any("reasoning_content" in d for d in deltas)
    assert translator.reasoning_content == []
