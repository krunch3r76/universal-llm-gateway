"""OpenAI Chat Completions SSE reducer (delta.content chunks).

Accumulates classic ``/v1/chat/completions`` streaming frames:

    data: {"id":"...","object":"chat.completion.chunk",
           "choices":[{"delta":{"content":"Hi"},"index":0}], ...}

    data: {"choices":[{"delta":{},"finish_reason":"stop"}], ...}

    data: [DONE]

Distinct from ``OpenAIResponsesReducer`` (Responses API ``response.completed``).
Used by master ``?pseudostream=true`` to force upstream SSE then return JSON.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sse.protocols import SSEMessage, SSEProviderError


@dataclass
class OpenAIChatCompletionsState:
    """Mutable reducer state for one chat.completions SSE stream."""

    content_parts: list[str] = field(default_factory=list)
    finish_reason: str | None = None
    model: str | None = None
    completion_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    saw_done: bool = False
    # Observability — prove multi-delta SSE vs one-shot JSON wrap
    sse_payload_events: int = 0
    delta_content_events: int = 0
    full_message_events: int = 0
    chunk_objects: list[str] = field(default_factory=list)


class OpenAIChatCompletionsReducer:
    """Reduce OpenAI chat.completion.chunk SSE events to a JSON completion.

    Terminal when ``data: [DONE]`` arrives, or when a payload carries a
    top-level ``error`` object (raises ``SSEProviderError`` via
    ``terminal_error`` only for ``event: error`` frames; inline error JSON
    is stored on state and terminates cleanly).
    """

    # 70B local polish can pause between tokens under load; default 90s is tight.
    DEFAULT_STALL_TIMEOUT: float = 300.0

    def initial_state(self) -> OpenAIChatCompletionsState:
        return OpenAIChatCompletionsState()

    def reduce(self, state: OpenAIChatCompletionsState, event: SSEMessage) -> bool:
        raw = event.data
        if isinstance(raw, str) and raw.strip() == "[DONE]":
            state.saw_done = True
            return True

        payload = _parse_payload(event)
        if not payload:
            return False

        state.sse_payload_events += 1
        obj = payload.get("object")
        if isinstance(obj, str) and obj and obj not in state.chunk_objects:
            state.chunk_objects.append(obj)

        if "error" in payload and isinstance(payload["error"], dict):
            state.error = payload["error"]
            return True

        if payload.get("id") and not state.completion_id:
            state.completion_id = str(payload["id"])
        if payload.get("model") and not state.model:
            state.model = str(payload["model"])

        usage = payload.get("usage")
        if isinstance(usage, dict) and usage:
            state.usage = usage

        for choice in payload.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            finish = choice.get("finish_reason")
            if finish:
                state.finish_reason = str(finish)
            delta = choice.get("delta")
            if isinstance(delta, dict):
                piece = delta.get("content")
                if isinstance(piece, str) and piece:
                    state.content_parts.append(piece)
                    state.delta_content_events += 1
            # Rare: some gateways put full message on a final chunk
            message = choice.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content:
                    state.full_message_events += 1
                    if not state.content_parts:
                        state.content_parts.append(content)

        return False

    def terminal_error(
        self, state: OpenAIChatCompletionsState, event: SSEMessage
    ) -> None:
        _ = state
        try:
            payload = _parse_payload(event)
        except Exception:
            payload = {"raw": event.data}
        raise SSEProviderError(f"OpenAI chat completions stream error: {payload}")

    @staticmethod
    def observability_headers(state: OpenAIChatCompletionsState) -> dict[str, str]:
        """Client-visible proof that upstream SSE was multi-delta vs one-shot."""
        objects = ",".join(state.chunk_objects) if state.chunk_objects else "none"
        return {
            "X-ULG-Pseudostream-Sse-Events": str(state.sse_payload_events),
            "X-ULG-Pseudostream-Delta-Parts": str(state.delta_content_events),
            "X-ULG-Pseudostream-Full-Message-Events": str(state.full_message_events),
            "X-ULG-Pseudostream-Chunk-Objects": objects,
            "X-ULG-Pseudostream-Saw-Done": "1" if state.saw_done else "0",
        }

    @staticmethod
    def to_chat_completion(
        state: OpenAIChatCompletionsState,
        *,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Build a non-stream ``chat.completion`` JSON body from reducer state."""
        content = "".join(state.content_parts)
        resolved_model = state.model or model or "unknown"
        completion_id = state.completion_id or f"chatcmpl-pseudo-{uuid.uuid4().hex[:24]}"
        body: dict[str, Any] = {
            "id": completion_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": resolved_model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": state.finish_reason or "stop",
                }
            ],
        }
        if state.usage:
            body["usage"] = state.usage
        if state.error is not None:
            body["error"] = state.error
        return body


def _parse_payload(event: SSEMessage) -> dict[str, Any]:
    if isinstance(event.data, dict):
        return event.data
    if not isinstance(event.data, str):
        return {}
    text = event.data.strip()
    if not text or text == "[DONE]":
        return {}
    try:
        loaded = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}
