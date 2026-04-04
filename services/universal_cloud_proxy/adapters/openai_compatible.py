from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from model_id import ModelId
from universal_event_bus.events.debug import emit_debug_event
from universal_logging import get_logger

from ..config import ProviderConfig

_APP_TITLE = "Stargate"
_APP_URL = "https://github.com/krunch3r76/universal-llm-gateway"
_MCP_SERVER_NAME = "vortex"

logger = get_logger(__name__)


class OpenAICompatibleAdapter:
    """Forward OpenAI-compatible provider requests while preserving model-ID mapping and upstream error semantics."""

    def __init__(self, *, config: ProviderConfig, client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = client

    @property
    def adapter_type(self) -> str:
        return "openai_compatible"

    @property
    def config(self) -> ProviderConfig:
        return self._config

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    def _emit_stream_debug(
        self,
        *,
        step: str,
        model_id: str,
        stream_start: float,
        mode: str,
        chunk_bytes: int | None = None,
        event_type: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "step": step,
            "model_id": model_id,
            "provider": self._config.provider,
            "mode": mode,
            "elapsed_ms": round((time.monotonic() - stream_start) * 1000.0, 1),
        }
        if chunk_bytes is not None:
            payload["chunk_bytes"] = chunk_bytes
        if event_type is not None:
            payload["event_type"] = event_type
        asyncio.create_task(
            emit_debug_event(
                "debug.cloud.stream",
                payload,
                source="cloud-proxy",
                scope="global",
            )
        )

    def normalize_catalog_model_id(self, raw_model_id: str) -> str:
        """Normalize provider model IDs into the catalog namespace.

        OpenRouter: prefix with openrouter/ so IDs are unambiguous.
        Native providers: bare provider/model (already the canonical form).
        Bare model names (no slash): prefix with provider name.
        """
        provider = self._config.provider.strip().lower()
        if provider == "openrouter":
            if raw_model_id.startswith("openrouter/"):
                return raw_model_id
            return f"openrouter/{raw_model_id}"
        if "/" in raw_model_id:
            return raw_model_id
        return f"{provider}/{raw_model_id}"

    def to_upstream_model_id(self, catalog_model_id: str) -> str:
        """Strip catalog namespace back to the ID the upstream API expects.

        OpenRouter expects provider/model. Native providers expect bare model name.
        """
        return ModelId.parse(catalog_model_id).api_model_id

    def _remote_mcp_tool(self) -> dict[str, Any]:
        auth_header = (
            f"Bearer {self._config.mcp_auth_token}"
            if self._config.mcp_auth_token
            else ""
        )
        tool: dict[str, Any] = {
            "type": "mcp",
            "server_url": str(self._config.mcp_server_url),
            "server_label": _MCP_SERVER_NAME,
        }
        if auth_header:
            tool["authorization"] = auth_header
        if self._config.provider.strip().lower() in {"openai", "chatgpt"}:
            tool["require_approval"] = "never"
        return tool

    def _prepare_chat_body(
        self, request_body: dict[str, Any], *, stream: bool | None = None
    ) -> dict[str, Any]:
        model_id = str(request_body.get("model", ""))
        parsed = ModelId.parse(model_id)
        body = {
            **request_body,
            "model": self.to_upstream_model_id(model_id),
        }
        if stream is not None:
            body["stream"] = stream

        inject_mcp = (
            self._config.mcp_server_url
            and parsed.is_mcp
            and body.get("tool_choice") != "none"
        )
        if not inject_mcp:
            return body

        tools_in = body.get("tools")
        tools_out = (
            [t for t in tools_in if isinstance(t, dict)]
            if isinstance(tools_in, list)
            else []
        )
        if not any(
            t.get("type") == "mcp"
            and t.get("server_url") == self._config.mcp_server_url
            for t in tools_out
        ):
            tools_out.append(self._remote_mcp_tool())
        body["tools"] = tools_out
        return body

    def _is_responses_api_request(self, request_body: dict[str, Any]) -> bool:
        """True when this request should route via the Responses API (xAI MCP)."""
        if self._config.provider.strip().lower() != "xai":
            return False
        model_id = str(request_body.get("model", ""))
        parsed = ModelId.parse(model_id)
        return bool(
            self._config.mcp_server_url
            and parsed.is_mcp
            and request_body.get("tool_choice") != "none"
        )

    @staticmethod
    def _convert_content_for_responses(
        content: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Convert OpenAI chat-completions content parts to Responses API format.

        Chat completions uses ``type: "text"`` / ``type: "image_url"`` with a
        nested ``image_url.url`` field.  The xAI Responses API expects
        ``type: "input_text"`` / ``type: "input_image"`` with ``image_url`` as
        a flat URL string.
        """
        converted: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                converted.append(part)
                continue
            part_type = part.get("type", "")
            if part_type == "text":
                converted.append({"type": "input_text", "text": part.get("text", "")})
            elif part_type == "image_url":
                img = part.get("image_url", {})
                url = img.get("url", "") if isinstance(img, dict) else str(img)
                item: dict[str, Any] = {"type": "input_image", "image_url": url}
                detail = img.get("detail") if isinstance(img, dict) else None
                if detail:
                    item["detail"] = detail
                converted.append(item)
            else:
                converted.append(part)
        return converted

    def _build_responses_body(self, request_body: dict[str, Any]) -> dict[str, Any]:
        """Build Responses API body with remote MCP tools for xAI.

        Per https://docs.x.ai/developers/tools/remote-mcp: xAI supports remote
        MCP on the Responses API (``/v1/responses``), not on chat completions.
        ``require_approval`` is not supported by xAI.

        This helper prepares the upstream xAI-native body. It does not imply
        that ``/v1/chat/completions`` becomes a passthrough surface; the
        OpenAI-compatible route still must translate request/response shapes.
        """
        model_id = str(request_body.get("model", ""))
        upstream_model = self.to_upstream_model_id(model_id)

        input_msgs: list[dict[str, Any]] = []
        for msg in request_body.get("messages", []):
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, list):
                msg = {**msg, "content": self._convert_content_for_responses(content)}
            input_msgs.append(msg)

        mcp_tool: dict[str, Any] = {
            "type": "mcp",
            "server_url": str(self._config.mcp_server_url),
            "server_label": _MCP_SERVER_NAME,
        }
        if self._config.mcp_auth_token:
            mcp_tool["authorization"] = f"Bearer {self._config.mcp_auth_token}"

        tools: list[dict[str, Any]] = [mcp_tool]
        for t in request_body.get("tools") or []:
            if not isinstance(t, dict) or t.get("type") == "mcp":
                continue
            if t.get("type") == "function" and isinstance(t.get("function"), dict):
                fn = t["function"]
                tools.append(
                    {
                        "type": "function",
                        "name": fn.get("name", ""),
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {}),
                    }
                )

        body: dict[str, Any] = {
            "model": upstream_model,
            "input": input_msgs,
            "tools": tools,
            "store": False,
        }

        max_tokens = request_body.get("max_tokens")
        if isinstance(max_tokens, int) and max_tokens > 0:
            body["max_output_tokens"] = max_tokens
        tool_choice = request_body.get("tool_choice")
        if tool_choice is not None and tool_choice != "none":
            body["tool_choice"] = tool_choice
        return body

    def _responses_to_chat_completion(
        self, resp_json: dict[str, Any], requested_model: str
    ) -> dict[str, Any]:
        """Convert Responses API JSON to OpenAI chat completion shape.

        Translates xAI/OpenAI Responses output items (message, function_call)
        into the OpenAI chat completion format including tool_calls.
        """
        text = resp_json.get("output_text", "")
        tool_calls: list[dict[str, Any]] = []

        for item in resp_json.get("output", []):
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "message" and not text:
                parts: list[str] = []
                for block in item.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "output_text":
                        parts.append(str(block.get("text", "")))
                text = "".join(parts)
            elif item_type == "function_call":
                call_id = (
                    item.get("call_id") or item.get("id") or f"call_{len(tool_calls)}"
                )
                tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": item.get("name", ""),
                            "arguments": item.get("arguments", "{}"),
                        },
                    }
                )

        usage_raw = resp_json.get("usage") or {}
        prompt_tokens = int(
            usage_raw.get("input_tokens") or usage_raw.get("prompt_tokens") or 0
        )
        completion_tokens = int(
            usage_raw.get("output_tokens") or usage_raw.get("completion_tokens") or 0
        )

        message: dict[str, Any] = {"role": "assistant", "content": text}
        if tool_calls:
            message["tool_calls"] = tool_calls

        return {
            "id": resp_json.get("id", f"chatcmpl-{int(time.time() * 1000)}"),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": requested_model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if tool_calls else "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    async def _forward_via_responses_api(
        self, request_body: dict[str, Any]
    ) -> dict[str, Any]:
        """Call xAI ``/responses`` and translate the final JSON to chat-completions.

        Used only behind the OpenAI-compatible ``/v1/chat/completions`` surface.
        The native passthrough surface is ``/api/v1/providers/xai/responses``.
        """
        body = self._build_responses_body(request_body)
        requested_model = str(request_body.get("model", ""))

        response = await self._client.post(
            f"{self._config.base_url}/responses",
            json=body,
            headers=self._headers(),
        )
        if response.status_code >= 400:
            await self._raise_provider_http_error(response)
        return self._responses_to_chat_completion(response.json(), requested_model)

    async def _forward_chat_translated_stream(
        self, request_body: dict[str, Any]
    ) -> AsyncIterator[bytes]:
        """Stream xAI Responses events and translate them incrementally.

        This is a streaming translator, not a raw passthrough. Upstream emits
        xAI/OpenAI Responses SSE events; this method converts them on the fly
        into OpenAI ``chat.completion.chunk`` frames for
        ``POST /v1/chat/completions`` compatibility.

        For direct byte-for-byte Responses streaming, use the native route
        ``/api/v1/providers/xai/responses`` instead.
        """
        body = self._build_responses_body(request_body)
        body["stream"] = True
        requested_model = str(request_body.get("model", ""))
        chunk_id = f"chatcmpl-{int(time.time() * 1000)}"
        created = int(time.time())
        sent_role = False
        first_delta_seen = False
        stream_start = time.monotonic()
        tool_call_index: dict[str, int] = {}
        has_tool_calls = False

        async with self._client.stream(
            "POST",
            f"{self._config.base_url}/responses",
            json=body,
            headers=self._headers(),
        ) as response:
            if response.status_code >= 400:
                await self._raise_provider_http_error(response)

            async for line in response.aiter_lines():
                stripped = line.strip()
                if not stripped or stripped.startswith("event:"):
                    continue
                if not stripped.startswith("data:"):
                    continue

                payload = stripped[len("data:") :].strip()
                if payload == "[DONE]":
                    break

                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                if event_type == "response.output_item.added":
                    item = event.get("item", {})
                    if isinstance(item, dict) and item.get("type") == "function_call":
                        has_tool_calls = True
                        idx = len(tool_call_index)
                        item_id = str(item.get("id", f"fc_{idx}"))
                        tool_call_index[item_id] = idx
                        call_id = item.get("call_id") or item_id
                        tc_chunk = {
                            "id": chunk_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": requested_model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": idx,
                                                "id": call_id,
                                                "type": "function",
                                                "function": {
                                                    "name": item.get("name", ""),
                                                    "arguments": "",
                                                },
                                            }
                                        ]
                                    },
                                    "finish_reason": None,
                                }
                            ],
                        }
                        yield f"data: {json.dumps(tc_chunk)}\n\n".encode()
                    elif (
                        isinstance(item, dict)
                        and item.get("type") == "message"
                        and item.get("role") == "assistant"
                        and not sent_role
                    ):
                        sent_role = True
                        role_chunk = {
                            "id": chunk_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": requested_model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"role": "assistant"},
                                    "finish_reason": None,
                                }
                            ],
                        }
                        yield f"data: {json.dumps(role_chunk)}\n\n".encode()

                elif event_type == "response.function_call_arguments.delta":
                    item_id = str(event.get("item_id", ""))
                    idx = tool_call_index.get(item_id)
                    if idx is not None:
                        args_chunk = {
                            "id": chunk_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": requested_model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": idx,
                                                "function": {
                                                    "arguments": event.get("delta", ""),
                                                },
                                            }
                                        ]
                                    },
                                    "finish_reason": None,
                                }
                            ],
                        }
                        yield f"data: {json.dumps(args_chunk)}\n\n".encode()

                elif event_type == "response.output_text.delta":
                    delta_text = event.get("delta", "")
                    if delta_text and not first_delta_seen:
                        first_delta_seen = True
                        self._emit_stream_debug(
                            step="firstdelta",
                            model_id=requested_model,
                            stream_start=stream_start,
                            mode="translated",
                            event_type=event_type,
                        )
                    delta: dict[str, str] = {"content": delta_text}
                    if not sent_role:
                        delta["role"] = "assistant"
                        sent_role = True
                    chunk = {
                        "id": chunk_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": requested_model,
                        "choices": [
                            {"index": 0, "delta": delta, "finish_reason": None}
                        ],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n".encode()

                elif event_type == "response.completed":
                    resp_data = event.get("response", {})
                    usage_raw = resp_data.get("usage") or {}
                    prompt_tokens = int(
                        usage_raw.get("input_tokens")
                        or usage_raw.get("prompt_tokens")
                        or 0
                    )
                    completion_tokens = int(
                        usage_raw.get("output_tokens")
                        or usage_raw.get("completion_tokens")
                        or 0
                    )
                    fr = "tool_calls" if has_tool_calls else "stop"
                    final_chunk = {
                        "id": chunk_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": requested_model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": fr}],
                        "usage": {
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": prompt_tokens + completion_tokens,
                        },
                    }
                    yield f"data: {json.dumps(final_chunk)}\n\n".encode()

        yield b"data: [DONE]\n\n"

    async def _forward_chat_passthrough_stream(
        self, request_body: dict[str, Any]
    ) -> AsyncIterator[bytes]:
        """Stream provider chat-completions bytes unchanged.

        This path is a transparent SSE relay. It preserves the upstream framing
        exactly as received so downstream clients observe the same event
        boundaries and flush behavior as the provider emitted.
        """
        requested_model = str(request_body.get("model", ""))
        body = self._prepare_chat_body(request_body, stream=True)
        stream_start = time.monotonic()
        first_chunk_seen = False
        async with self._client.stream(
            "POST",
            f"{self._config.base_url}/chat/completions",
            json=body,
            headers=self._headers(),
        ) as response:
            if response.status_code >= 400:
                await self._raise_provider_http_error(response)
            async for chunk in response.aiter_raw():
                if chunk:
                    if not first_chunk_seen:
                        first_chunk_seen = True
                        self._emit_stream_debug(
                            step="firstchunk",
                            model_id=requested_model,
                            stream_start=stream_start,
                            mode="passthrough",
                            chunk_bytes=len(chunk),
                        )
                    yield chunk

    async def forward_native(self, request_body: dict[str, Any]) -> dict[str, Any]:
        """POST Responses API JSON unchanged (native xAI/OpenAI-shaped ingress)."""
        response = await self._client.post(
            f"{self._config.base_url}/responses",
            json=request_body,
            headers=self._headers(),
        )
        if response.status_code >= 400:
            await self._raise_provider_http_error(response)
        return response.json()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": _APP_URL,
            "X-Title": _APP_TITLE,
        }

    async def _raise_provider_http_error(self, response: httpx.Response) -> None:
        """Raise HTTPStatusError with provider response body preserved for diagnostics."""
        error_body = await response.aread()
        error_preview = error_body.decode(errors="replace")[:500]
        logger.error(
            "OpenAI-compatible provider API %d: %s",
            response.status_code,
            error_preview,
        )
        raise httpx.HTTPStatusError(
            f"Provider returned {response.status_code}: {error_preview}",
            request=response.request,
            response=response,
        )

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
        """Forward a non-streaming chat request and preserve provider error payloads on HTTP failure."""
        if self._is_responses_api_request(request_body):
            return await self._forward_via_responses_api(request_body)
        body = self._prepare_chat_body(request_body)
        response = await self._client.post(
            f"{self._config.base_url}/chat/completions",
            json=body,
            headers=self._headers(),
        )
        if response.status_code >= 400:
            await self._raise_provider_http_error(response)
        return response.json()

    async def forward_chat_stream(
        self, request_body: dict[str, Any]
    ) -> AsyncIterator[bytes]:
        """Forward a streaming chat request via explicit passthrough or translation."""
        if self._is_responses_api_request(request_body):
            async for chunk in self._forward_chat_translated_stream(request_body):
                yield chunk
            return
        async for chunk in self._forward_chat_passthrough_stream(request_body):
            yield chunk

    async def forward_embeddings(self, request_body: dict[str, Any]) -> dict[str, Any]:
        """Forward an embeddings request and preserve provider error payloads on upstream HTTP failure."""
        body = {
            **request_body,
            "model": self.to_upstream_model_id(str(request_body.get("model", ""))),
        }
        response = await self._client.post(
            f"{self._config.base_url}/embeddings",
            json=body,
            headers=self._headers(),
        )
        if response.status_code >= 400:
            await self._raise_provider_http_error(response)
        return response.json()
