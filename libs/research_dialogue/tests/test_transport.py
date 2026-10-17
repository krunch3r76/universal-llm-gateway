from __future__ import annotations

import pytest

from research_dialogue.transport import _parse_sse_stream, merge_usage_into, send_messages

pytestmark = pytest.mark.offline


def test_refused_url() -> None:
    with pytest.raises(ValueError, match="refused URL"):
        send_messages({}, path="/other/path")


def test_sse_usage_merge_last_value_wins() -> None:
    events = [
        'data: {"type":"message_start","message":{"id":"m1","usage":{"input_tokens":10,"output_tokens":0}}}',
        "",
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}',
        "",
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"input_tokens":10,"output_tokens":3,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}',
        "",
    ]

    class R:
        status_code = 200

        def iter_lines(self):
            for line in events:
                yield line

    result = _parse_sse_stream(R())  # type: ignore[arg-type]
    assert result.text == "Hi"
    assert result.usage.get("input_tokens") == 10
    assert result.usage.get("output_tokens") == 3
    assert result.stop_reason == "end_turn"
    assert result.usage_incomplete is False


def test_sse_usage_incomplete_without_delta() -> None:
    events = [
        'data: {"type":"message_start","message":{"id":"m1","usage":{"input_tokens":10,"output_tokens":0}}}',
        "",
    ]

    class R:
        status_code = 200

        def iter_lines(self):
            for line in events:
                yield line

    result = _parse_sse_stream(R())  # type: ignore[arg-type]
    assert result.usage_incomplete is True
    assert result.delta_usage_seen is False


def test_non_stream_json_parse() -> None:
    payload = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Hello"}],
        "model": "claude-sonnet-5",
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 5,
            "output_tokens": 2,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }

    class R:
        status_code = 200

        def json(self):
            return payload

        text = ""

    from research_dialogue.transport import _parse_non_stream_json

    result = _parse_non_stream_json(R())  # type: ignore[arg-type]
    assert result.text == "Hello"
    assert result.usage["output_tokens"] == 2
    assert result.usage_incomplete is False


def test_merge_usage_records_skipped_dict_keys() -> None:
    usage: dict = {}
    skipped = merge_usage_into(
        usage,
        {"input_tokens": 1, "output_tokens_details": {"a": 1}},
    )
    assert "output_tokens_details" in skipped
    assert "input_tokens" in usage


def test_refused_stargate_host() -> None:
    from research_dialogue.transport import assert_allowed_stargate_base

    with pytest.raises(ValueError, match="refused"):
        assert_allowed_stargate_base("http://evil.example:9999")


def test_merge_usage_matches_gate_tap() -> None:
    """message_start + message_delta must not sum ints; delta overwrites."""
    usage: dict = {}
    merge_usage_into(
        usage,
        {
            "input_tokens": 100,
            "cache_creation_input_tokens": 50,
            "cache_creation": {"ephemeral_5m_input_tokens": 50},
            "output_tokens": 1,
        },
    )
    merge_usage_into(
        usage,
        {
            "input_tokens": 100,
            "output_tokens": 300,
        },
    )
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 300
    assert usage["cache_creation_input_tokens"] == 50
    assert usage["cache_creation"] == {"ephemeral_5m_input_tokens": 50}
