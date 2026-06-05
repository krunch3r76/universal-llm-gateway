"""Tests for Gemini MCP tool-loop fidelity helpers."""

from __future__ import annotations

import json

from llm_adapters.google import GoogleAdapter
from llm_adapters.google_replay import normalize_gemini_parts, replay_model_turn_content
from llm_adapters.google_tool_response import build_function_response_payload


def test_build_function_response_parses_json_object() -> None:
    payload = build_function_response_payload(
        json.dumps({"ok": True, "status": "success", "items": [1, 2, 3]})
    )
    assert payload["ok"] is True
    assert payload["status"] == "success"
    assert isinstance(payload["data"], dict)
    assert payload["data"]["items"] == [1, 2, 3]
    assert payload["error"] is None
    assert "result" not in payload


def test_build_function_response_marks_error_envelope() -> None:
    payload = build_function_response_payload(
        json.dumps({"ok": False, "error": {"message": "permission denied"}})
    )
    assert payload["ok"] is False
    assert payload["error"] == "permission denied"


def test_build_function_response_plain_text_fallback() -> None:
    payload = build_function_response_payload("hello world")
    assert payload["ok"] is True
    assert payload["data"] == {"text": "hello world"}


def test_build_function_response_preserves_large_fs_read_content() -> None:
    prefix = "x" * 8500
    sentinel = "SENTINEL_AT_CHAR_8500"
    suffix = "y" * 500
    content = prefix + sentinel + suffix
    assert len(content) > 9000

    payload = build_function_response_payload(
        json.dumps({"content": content, "path": "/tmp/spec.md"})
    )

    data = payload["data"]
    assert isinstance(data, dict)
    assert data["content"] == content
    assert len(data["content"]) == len(content)
    assert sentinel in data["content"]
    assert data["path"] == "/tmp/spec.md"
    assert "truncated" not in data


def test_normalize_gemini_parts_drops_empty_text() -> None:
    parts = [{"text": ""}, {"functionCall": {"name": "fs", "args": {}}}]
    normalized = normalize_gemini_parts(parts)
    assert len(normalized) == 1
    assert "functionCall" in normalized[0]


def test_replay_model_turn_preserves_thought_signature() -> None:
    raw = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {
                                "id": "call-1",
                                "name": "cortex",
                                "args": {"tool": "entity_get"},
                            },
                            "thoughtSignature": "sig-abc",
                        }
                    ],
                }
            }
        ]
    }
    replay = replay_model_turn_content(raw)
    assert replay is not None
    part = replay["parts"][0]
    assert part["thoughtSignature"] == "sig-abc"
    assert part["functionCall"]["id"] == "call-1"


def test_append_tool_round_emits_structured_function_response() -> None:
    adapter = GoogleAdapter(api_key="test")
    body: dict = {"contents": []}
    raw = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {
                                "name": "cortex",
                                "args": {"tool": "stats"},
                            }
                        }
                    ],
                }
            }
        ]
    }
    tool_results = [
        {
            "id": "call-1",
            "name": "cortex",
            "content": json.dumps({"ok": True, "count": 3}),
        }
    ]
    adapter.append_tool_round(body, raw, tool_results)

    user_turn = body["contents"][-1]
    fr = user_turn["parts"][0]["functionResponse"]
    assert fr["name"] == "cortex"
    assert fr["id"] == "call-1"
    response = fr["response"]
    assert isinstance(response, dict)
    assert response["ok"] is True
    assert response["data"]["count"] == 3
    assert "result" not in response
