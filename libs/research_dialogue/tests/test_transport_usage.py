from __future__ import annotations

import json

import pytest
from research_dialogue.transport import (
    _parse_non_stream_json,
    _parse_sse_stream,
    merge_usage_into,
    stream_usage_complete,
)

pytestmark = pytest.mark.offline

_WELL_FORMED_USAGE = {
    "input_tokens": 5,
    "output_tokens": 2,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
}


def _non_stream_response(usage: dict) -> object:
    payload = {
        "id": "msg_1",
        "content": [{"type": "text", "text": "x"}],
        "stop_reason": "end_turn",
        "usage": usage,
    }

    class R:
        status_code = 200

        def json(self):
            return payload

        text = ""

    return R()


def _sse_lines(*events: dict) -> list[str]:
    lines: list[str] = []
    for ev in events:
        lines.append(f"data: {json.dumps(ev)}")
        lines.append("")
    return lines


def _sse_response(lines: list[str]) -> object:
    class R:
        status_code = 200

        def iter_lines(self):
            yield from lines

    return R()


@pytest.mark.parametrize("bad_value", [True, "4", None])
@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
def test_non_stream_invalid_priced_token_field(bad_value: object, field: str) -> None:
    usage = dict(_WELL_FORMED_USAGE)
    usage[field] = bad_value
    result = _parse_non_stream_json(_non_stream_response(usage))  # type: ignore[arg-type]
    assert field not in result.usage
    assert result.usage_incomplete is True
    assert result.usage_invalid_keys is not None
    assert field in result.usage_invalid_keys


@pytest.mark.parametrize("bad_value", [True, "4", None])
@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
def test_sse_invalid_priced_token_field(bad_value: object, field: str) -> None:
    start_usage = dict(_WELL_FORMED_USAGE)
    start_usage[field] = bad_value
    delta_usage = dict(_WELL_FORMED_USAGE)
    delta_usage[field] = bad_value
    lines = _sse_lines(
        {"type": "message_start", "message": {"id": "m1", "usage": start_usage}},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": delta_usage,
        },
    )
    result = _parse_sse_stream(_sse_response(lines))  # type: ignore[arg-type]
    assert result.usage_incomplete is True
    assert result.usage_invalid_keys is not None
    assert field in result.usage_invalid_keys


def test_sse_invalid_output_tokens_does_not_clobber_valid_start() -> None:
    lines = _sse_lines(
        {
            "type": "message_start",
            "message": {"id": "m1", "usage": {"output_tokens": 5, "input_tokens": 1}},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {
                "output_tokens": True,
                "input_tokens": 1,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        },
    )
    result = _parse_sse_stream(_sse_response(lines))  # type: ignore[arg-type]
    assert result.usage.get("output_tokens") == 5
    assert result.usage_incomplete is True
    assert result.usage_invalid_keys is not None
    assert "output_tokens" in result.usage_invalid_keys


def test_merge_cache_creation_partial_invalid_subvalues() -> None:
    usage: dict = {}
    invalid: list[str] = []
    merge_usage_into(
        usage,
        {
            "cache_creation": {
                "ephemeral_5m_input_tokens": True,
                "ephemeral_1h_input_tokens": 3,
            }
        },
        invalid=invalid,
    )
    assert usage["cache_creation"] == {"ephemeral_1h_input_tokens": 3}
    assert "cache_creation" in invalid


def test_merge_cache_creation_non_dict_invalid() -> None:
    usage: dict = {}
    invalid: list[str] = []
    merge_usage_into(usage, {"cache_creation": 1}, invalid=invalid)
    assert "cache_creation" not in usage
    assert invalid == ["cache_creation"]


def test_merge_server_tool_use_drops_bool_subvalue() -> None:
    usage: dict = {}
    merge_usage_into(
        usage,
        {"server_tool_use": {"web_search_requests": True, "accepted": 2}},
    )
    assert usage["server_tool_use"] == {"accepted": 2}


def test_well_formed_usage_still_complete() -> None:
    usage: dict = {}
    invalid: list[str] = []
    merge_usage_into(usage, _WELL_FORMED_USAGE, invalid=invalid)
    assert not invalid
    assert stream_usage_complete(usage)
    result = _parse_non_stream_json(_non_stream_response(_WELL_FORMED_USAGE))  # type: ignore[arg-type]
    assert result.usage_incomplete is False
    assert result.usage_invalid_keys is None


def test_read_timeout_constant_is_1800() -> None:
    from research_dialogue import transport

    assert transport._READ_TIMEOUT_S == 1800.0
