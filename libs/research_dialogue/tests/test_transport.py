from __future__ import annotations

import json
from io import BytesIO

import httpx
import pytest

from research_dialogue.transport import _parse_sse_stream, send_messages

pytestmark = pytest.mark.offline


def test_refused_url() -> None:
    with pytest.raises(ValueError, match="refused URL"):
        send_messages({}, path="/other/path")


def test_sse_usage_merge() -> None:
    events = [
        'data: {"type":"message_start","message":{"id":"m1","usage":{"input_tokens":10,"output_tokens":0}}}',
        "",
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}',
        "",
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3}}',
        "",
    ]
    raw = "\n".join(events).encode()
    resp = httpx.Response(200, request=httpx.Request("POST", "http://x"), stream=BytesIO(raw))
    resp._stream = BytesIO(raw)  # type: ignore[attr-defined]
    # httpx Response.iter_lines needs a request with stream — use manual parse helper
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
