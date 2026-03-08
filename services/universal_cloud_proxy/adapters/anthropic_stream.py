"""Anthropic SSE stream-to-OpenAI chunk translator.

Stateful: one StreamTranslator per streaming request. Handles text deltas,
tool_use start/delta events, and finish reason mapping.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx


class StreamTranslator:
    """Translates Anthropic SSE events into OpenAI streaming chunks."""

    def __init__(self, model_id: str) -> None:
        self._model_id = model_id
        self._completion_id = f"chatcmpl-anthropic-{int(time.time() * 1000)}"
        self._created = int(time.time())
        self._tool_index_map: dict[int, int] = {}
        self._tool_meta: dict[int, dict[str, str]] = {}
        self._current_event = ""
        self._finish_emitted = False

    def _chunk(self, delta: dict[str, Any], finish: str | None = None) -> bytes:
        payload = {
            "id": self._completion_id,
            "object": "chat.completion.chunk",
            "created": self._created,
            "model": self._model_id,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(payload)}\n\n".encode()

    def process_line(
        self,
        line: str,
        *,
        request: httpx.Request | None = None,
        response: httpx.Response | None = None,
    ) -> list[bytes]:
        """Process one SSE line, returning zero or more OpenAI SSE chunks.

        Raises httpx.HTTPStatusError on Anthropic stream errors.
        """
        stripped = line.strip()
        if not stripped:
            return []
        if stripped.startswith("event:"):
            self._current_event = stripped.split(":", 1)[1].strip()
            return []
        if not stripped.startswith("data:"):
            return []

        payload_raw = stripped.split(":", 1)[1].strip()
        if payload_raw == "[DONE]":
            return [b"data: [DONE]\n\n"]

        payload = json.loads(payload_raw)
        event = self._current_event or str(payload.get("type", ""))

        match event:
            case "message_start":
                return self._on_message_start(payload)
            case "content_block_start":
                return self._on_content_block_start(payload)
            case "content_block_delta":
                return self._on_content_block_delta(payload)
            case "message_stop":
                return self._on_message_stop()
            case "error":
                self._raise_error(payload, request, response)
            case _:
                return []
        return []

    def finalize(self) -> list[bytes]:
        if not self._finish_emitted:
            return [b"data: [DONE]\n\n"]
        return []

    def _on_message_start(self, payload: dict[str, Any]) -> list[bytes]:
        msg = payload.get("message", {})
        if isinstance(msg, dict) and isinstance(msg.get("id"), str):
            self._completion_id = msg["id"]
        return [self._chunk({"role": "assistant"})]

    def _on_content_block_start(self, payload: dict[str, Any]) -> list[bytes]:
        block = payload.get("content_block", {})
        content_index = payload.get("index")
        if not isinstance(block, dict) or not isinstance(content_index, int):
            return []

        block_type = str(block.get("type", ""))

        # server_tool_use (e.g. dynamic filtering code execution inside
        # web_search_20260209) runs on Anthropic's servers — the client never
        # executes it and must not receive it as a tool_calls chunk.
        if block_type == "server_tool_use":
            return []

        if block_type != "tool_use":
            return []

        tool_idx = len(self._tool_index_map)
        self._tool_index_map[content_index] = tool_idx

        tool_id = block.get("id")
        tool_name = block.get("name")
        if not isinstance(tool_id, str):
            tool_id = f"call_{tool_idx}"
        if not isinstance(tool_name, str):
            tool_name = ""

        self._tool_meta[tool_idx] = {"id": tool_id, "name": tool_name}

        return [
            self._chunk(
                {
                    "tool_calls": [
                        {
                            "index": tool_idx,
                            "id": tool_id,
                            "type": "function",
                            "function": {"name": tool_name, "arguments": ""},
                        }
                    ]
                }
            )
        ]

    def _on_content_block_delta(self, payload: dict[str, Any]) -> list[bytes]:
        delta = payload.get("delta", {})
        if not isinstance(delta, dict):
            return []

        text = delta.get("text")
        if isinstance(text, str) and text:
            return [self._chunk({"content": text})]

        content_index = payload.get("index")
        if (
            isinstance(content_index, int)
            and delta.get("type") == "input_json_delta"
            and isinstance(delta.get("partial_json"), str)
        ):
            tool_idx = self._tool_index_map.get(content_index)
            if tool_idx is None:
                return []

            meta = self._tool_meta.get(tool_idx, {})
            return [
                self._chunk(
                    {
                        "tool_calls": [
                            {
                                "index": tool_idx,
                                "id": meta.get("id", f"call_{tool_idx}"),
                                "type": "function",
                                "function": {
                                    "name": meta.get("name", ""),
                                    "arguments": delta["partial_json"],
                                },
                            }
                        ]
                    }
                )
            ]

        return []

    def _on_message_stop(self) -> list[bytes]:
        self._finish_emitted = True
        fr = "tool_calls" if self._tool_meta else "stop"
        return [self._chunk({}, finish=fr), b"data: [DONE]\n\n"]

    @staticmethod
    def _raise_error(
        payload: dict[str, Any],
        request: httpx.Request | None,
        response: httpx.Response | None,
    ) -> None:
        error_msg = payload.get("error", payload)
        if request is not None and response is not None:
            raise httpx.HTTPStatusError(
                f"Provider returned streaming error: {error_msg}",
                request=request,
                response=response,
            )
        raise RuntimeError(f"Provider returned streaming error: {error_msg}")
