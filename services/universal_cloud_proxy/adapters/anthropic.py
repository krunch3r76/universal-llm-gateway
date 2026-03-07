from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..config import ProviderConfig

logger = logging.getLogger(__name__)

_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_DEFAULT_MAX_TOKENS = 4096


class AnthropicAdapter:
    def __init__(self, *, config: ProviderConfig, client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = client

    @property
    def adapter_type(self) -> str:
        return "anthropic"

    @property
    def config(self) -> ProviderConfig:
        return self._config

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    def normalize_catalog_model_id(self, raw_model_id: str) -> str:
        if raw_model_id.startswith("native/anthropic/"):
            return raw_model_id
        if raw_model_id.startswith("native/"):
            return raw_model_id
        if "/" in raw_model_id:
            return f"native/{raw_model_id}"
        return f"native/anthropic/{raw_model_id}"

    def to_upstream_model_id(self, catalog_model_id: str) -> str:
        if catalog_model_id.startswith("native/anthropic/"):
            return catalog_model_id.removeprefix("native/anthropic/")
        if catalog_model_id.startswith("native/"):
            return catalog_model_id.removeprefix("native/")
        if catalog_model_id.startswith("anthropic/"):
            return catalog_model_id.removeprefix("anthropic/")
        return catalog_model_id

    @staticmethod
    def _coerce_content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    if block.get("type") == "text" and isinstance(block.get("text"), str):
                        parts.append(block["text"])
                    elif isinstance(block.get("content"), str):
                        parts.append(block["content"])
            return "\n".join(part for part in parts if part)
        return str(content) if content is not None else ""

    def _openai_to_anthropic(self, request_body: dict[str, Any]) -> dict[str, Any]:
        model_id = str(request_body.get("model", "")).strip()
        anthropic_model = self.to_upstream_model_id(model_id)

        raw_messages = request_body.get("messages")
        messages: list[dict[str, Any]] = []
        system_parts: list[str] = []
        if isinstance(raw_messages, list):
            for item in raw_messages:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role", "user"))
                content = self._coerce_content_to_text(item.get("content", ""))
                if role == "system":
                    if content:
                        system_parts.append(content)
                    continue
                if role not in {"user", "assistant"}:
                    role = "user"
                messages.append({"role": role, "content": content})

        max_tokens = request_body.get("max_tokens")
        if not isinstance(max_tokens, int):
            max_tokens = request_body.get("max_completion_tokens")
        if not isinstance(max_tokens, int):
            max_tokens = _ANTHROPIC_DEFAULT_MAX_TOKENS
            logger.warning(
                "Anthropic request missing max_tokens/max_completion_tokens; "
                "using default %d",
                _ANTHROPIC_DEFAULT_MAX_TOKENS,
            )

        payload: dict[str, Any] = {
            "model": anthropic_model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        for key in ("temperature", "top_p", "top_k"):
            value = request_body.get(key)
            if value is not None:
                payload[key] = value
        stop = request_body.get("stop")
        if isinstance(stop, str):
            payload["stop_sequences"] = [stop]
        elif isinstance(stop, list):
            payload["stop_sequences"] = [s for s in stop if isinstance(s, str)]
        if bool(request_body.get("stream", False)):
            payload["stream"] = True
        return payload

    @staticmethod
    def _anthropic_text(content: Any) -> str:
        if not isinstance(content, list):
            return ""
        return "".join(
            item["text"]
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        )

    @staticmethod
    def _finish_reason(stop_reason: str | None) -> str | None:
        mapping = {
            "end_turn": "stop",
            "stop_sequence": "stop",
            "max_tokens": "length",
            "tool_use": "tool_calls",
        }
        if stop_reason is None:
            return None
        return mapping.get(stop_reason, "stop")

    @staticmethod
    def _to_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _anthropic_to_openai_response(
        self,
        response_json: dict[str, Any],
        requested_model_id: str,
    ) -> dict[str, Any]:
        raw_usage = response_json.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        prompt_tokens = self._to_int(usage.get("input_tokens", 0))
        completion_tokens = self._to_int(usage.get("output_tokens", 0))
        stop_reason = response_json.get("stop_reason")
        finish_reason = self._finish_reason(
            stop_reason if isinstance(stop_reason, str) else None
        )
        return {
            "id": response_json.get("id", f"chatcmpl-{int(time.time() * 1000)}"),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": requested_model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": self._anthropic_text(response_json.get("content")),
                    },
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    @staticmethod
    def _sse_data(payload: dict[str, Any]) -> bytes:
        return f"data: {json.dumps(payload)}\n\n".encode()

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._config.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

    async def fetch_catalog(self) -> list[dict[str, Any]]:
        response = await self._client.get(
            f"{self._config.base_url}/models",
            headers=self._headers(),
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        data = body.get("data", [])
        return data if isinstance(data, list) else []

    async def forward_chat(self, request_body: dict[str, Any]) -> dict[str, Any]:
        body = self._openai_to_anthropic(request_body)
        response = await self._client.post(
            f"{self._config.base_url}/messages",
            json=body,
            headers=self._headers(),
        )
        response.raise_for_status()
        return self._anthropic_to_openai_response(
            response.json(), str(request_body.get("model", ""))
        )

    async def forward_chat_stream(
        self, request_body: dict[str, Any]
    ) -> AsyncIterator[bytes]:
        body = self._openai_to_anthropic({**request_body, "stream": True})
        async with self._client.stream(
            "POST",
            f"{self._config.base_url}/messages",
            json=body,
            headers=self._headers(),
        ) as response:
            if response.status_code >= 400:
                error_body = await response.aread()
                error_preview = error_body.decode(errors="replace")[:300]
                raise httpx.HTTPStatusError(
                    f"Provider returned {response.status_code}: {error_preview}",
                    request=response.request,
                    response=response,
                )

            created = int(time.time())
            completion_id = f"chatcmpl-anthropic-{int(time.time() * 1000)}"
            finish_emitted = False
            current_event = ""
            async for line in response.aiter_lines():
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("event:"):
                    current_event = stripped.split(":", 1)[1].strip()
                    continue
                if not stripped.startswith("data:"):
                    continue
                payload_raw = stripped.split(":", 1)[1].strip()
                if payload_raw == "[DONE]":
                    yield b"data: [DONE]\n\n"
                    return
                payload_json = json.loads(payload_raw)
                event_type = current_event or str(payload_json.get("type", ""))
                delta_payload = payload_json.get("delta", {})

                if event_type == "message_start":
                    message = payload_json.get("message", {})
                    if isinstance(message, dict):
                        completion_id = str(message.get("id", completion_id))
                    yield self._sse_data(
                        {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": request_body.get("model", ""),
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"role": "assistant"},
                                    "finish_reason": None,
                                }
                            ],
                        }
                    )
                    continue

                if event_type == "content_block_delta":
                    text = None
                    if isinstance(delta_payload, dict):
                        text = delta_payload.get("text")
                    if text is None:
                        text = payload_json.get("text")
                    if isinstance(text, str) and text:
                        yield self._sse_data(
                            {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": request_body.get("model", ""),
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {"content": text},
                                        "finish_reason": None,
                                    }
                                ],
                            }
                        )
                    continue

                if event_type == "message_delta":
                    stop_reason = (
                        delta_payload.get("stop_reason")
                        if isinstance(delta_payload, dict)
                        else None
                    )
                    stop_reason = (
                        stop_reason
                        if stop_reason is not None
                        else payload_json.get("stop_reason")
                    )
                    finish_reason = self._finish_reason(
                        stop_reason
                        if isinstance(stop_reason, str) or stop_reason is None
                        else None
                    )
                    if finish_reason is not None:
                        finish_emitted = True
                        yield self._sse_data(
                            {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": request_body.get("model", ""),
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {},
                                        "finish_reason": finish_reason,
                                    }
                                ],
                            }
                        )
                    continue

                if event_type == "error":
                    message = payload_json.get("error", payload_json)
                    raise httpx.HTTPStatusError(
                        f"Provider returned streaming error: {message}",
                        request=response.request,
                        response=response,
                    )

                if event_type == "message_stop":
                    if not finish_emitted:
                        yield self._sse_data(
                            {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": request_body.get("model", ""),
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {},
                                        "finish_reason": "stop",
                                    }
                                ],
                            }
                        )
                    yield b"data: [DONE]\n\n"
                    return
            yield b"data: [DONE]\n\n"

    async def forward_embeddings(self, request_body: dict[str, Any]) -> dict[str, Any]:
        raise ValueError(
            "Provider 'anthropic' does not support OpenAI embeddings forwarding"
        )
