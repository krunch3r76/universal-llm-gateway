"""Anthropic SSE reducer to raw Anthropic message dict shape."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sse.protocols import SSEMessage, SSEProviderError


@dataclass
class _AnthropicState:
    """Mutable reducer state for a single Anthropic stream."""

    content: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(
        default_factory=lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "cache_read_input_tokens": 0,
        }
    )
    model: str = ""
    id: str = ""
    stop_reason: str | None = None
    _block_type: str | None = None
    _text_buf: str = ""
    _partial_json: str = ""
    _thinking_buf: str = ""
    _thinking_signature: str | None = None
    _tool_id: str | None = None
    _tool_name: str | None = None
    _redacted_data: str | None = None


class AnthropicReducer:
    """Reduce Anthropic SSE events to raw Anthropic Messages API response shape."""

    DEFAULT_STALL_TIMEOUT: float = 60.0

    def initial_state(self) -> _AnthropicState:
        return _AnthropicState()

    def reduce(self, state: _AnthropicState, event: SSEMessage) -> bool:
        payload = self._parse_payload(event)
        event_type = str(payload.get("type") or "")
        if not event_type:
            return False

        if event_type == "message_start":
            self._on_message_start(state, payload)
        elif event_type == "content_block_start":
            self._on_content_block_start(state, payload)
        elif event_type == "content_block_delta":
            self._on_content_block_delta(state, payload)
        elif event_type == "content_block_stop":
            self._on_content_block_stop(state)
        elif event_type == "message_delta":
            self._on_message_delta(state, payload)
        elif event_type == "message_stop":
            return True
        return False

    def terminal_error(self, state: _AnthropicState, event: SSEMessage) -> None:
        _ = state
        try:
            payload = self._parse_payload(event)
        except Exception:
            payload = {"raw": event.data}
        raise SSEProviderError(f"Anthropic stream error: {payload}")

    @staticmethod
    def to_terminal_dict(state: _AnthropicState) -> dict[str, Any]:
        """Convert reducer state to raw Anthropic response dict shape."""
        return {
            "id": state.id,
            "model": state.model,
            "stop_reason": state.stop_reason,
            "type": "message",
            "content": state.content,
            "usage": state.usage,
        }

    @staticmethod
    def _parse_payload(event: SSEMessage) -> dict[str, Any]:
        if isinstance(event.data, dict):
            return event.data
        try:
            loaded = json.loads(event.data)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def _on_message_start(state: _AnthropicState, payload: dict[str, Any]) -> None:
        message = payload.get("message")
        if not isinstance(message, dict):
            return
        state.id = str(message.get("id") or "")
        state.model = str(message.get("model") or "")
        state.stop_reason = message.get("stop_reason")
        usage = message.get("usage")
        if isinstance(usage, dict):
            state.usage["input_tokens"] = int(usage.get("input_tokens") or 0)

    @staticmethod
    def _on_content_block_start(
        state: _AnthropicState, payload: dict[str, Any]
    ) -> None:
        block = payload.get("content_block")
        if not isinstance(block, dict):
            return
        block_type = str(block.get("type") or "")
        state._block_type = block_type or None
        state._text_buf = ""
        state._partial_json = ""
        state._thinking_buf = ""
        state._thinking_signature = None
        state._tool_id = None
        state._tool_name = None
        state._redacted_data = None

        if block_type == "tool_use":
            state._tool_id = str(block.get("id") or "")
            state._tool_name = str(block.get("name") or "")
        elif block_type == "thinking":
            signature = block.get("signature")
            state._thinking_signature = (
                str(signature) if isinstance(signature, str) else None
            )
        elif block_type == "redacted_thinking":
            redacted = block.get("data")
            if isinstance(redacted, str):
                state._redacted_data = redacted

    @staticmethod
    def _on_content_block_delta(
        state: _AnthropicState, payload: dict[str, Any]
    ) -> None:
        delta = payload.get("delta")
        if not isinstance(delta, dict):
            return
        delta_type = str(delta.get("type") or "")
        if delta_type == "text_delta":
            state._text_buf += str(delta.get("text") or "")
        elif delta_type == "input_json_delta":
            state._partial_json += str(delta.get("partial_json") or "")
        elif delta_type == "thinking_delta":
            state._thinking_buf += str(delta.get("thinking") or "")
        elif delta_type == "signature_delta":
            signature = delta.get("signature")
            state._thinking_signature = (
                str(signature) if isinstance(signature, str) else None
            )

    @staticmethod
    def _on_content_block_stop(state: _AnthropicState) -> None:
        block = AnthropicReducer._finalize_block(state)
        if block is not None:
            state.content.append(block)
        state._block_type = None
        state._text_buf = ""
        state._partial_json = ""
        state._thinking_buf = ""
        state._thinking_signature = None
        state._tool_id = None
        state._tool_name = None
        state._redacted_data = None

    @staticmethod
    def _finalize_block(state: _AnthropicState) -> dict[str, Any] | None:
        if state._block_type == "text":
            return {"type": "text", "text": state._text_buf}
        if state._block_type == "tool_use":
            assembled_input: dict[str, Any]
            partial = state._partial_json.strip()
            if not partial:
                assembled_input = {}
            else:
                try:
                    loaded = json.loads(partial)
                except json.JSONDecodeError:
                    assembled_input = {}
                else:
                    assembled_input = loaded if isinstance(loaded, dict) else {}
            return {
                "type": "tool_use",
                "id": state._tool_id,
                "name": state._tool_name,
                "input": assembled_input,
            }
        if state._block_type == "thinking":
            return {
                "type": "thinking",
                "thinking": state._thinking_buf,
                "signature": state._thinking_signature,
            }
        if state._block_type == "redacted_thinking":
            return {"type": "redacted_thinking", "data": state._redacted_data or ""}
        return None

    @staticmethod
    def _on_message_delta(state: _AnthropicState, payload: dict[str, Any]) -> None:
        delta = payload.get("delta")
        if isinstance(delta, dict):
            stop_reason = delta.get("stop_reason")
            state.stop_reason = str(stop_reason) if stop_reason is not None else None
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return
        state.usage["output_tokens"] = int(usage.get("output_tokens") or 0)
        state.usage["thinking_tokens"] = int(usage.get("thinking_tokens") or 0)
        state.usage["cache_read_input_tokens"] = int(
            usage.get("cache_read_input_tokens") or 0
        )
