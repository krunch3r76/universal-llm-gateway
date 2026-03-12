from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from universal_event_bus import EventBus

from ..config import ProviderConfig
from ..events import (
    McpAdapterMcpToolUseSeen,
    McpAdapterToolSearchSeen,
)
from .anthropic_format import (
    build_native_tools,
    convert_messages,
    convert_tool_choice,
    convert_tools,
    dedupe_tools,
    extract_system_text,
)
from .anthropic_response import convert_response_content
from .anthropic_stream import StreamTranslator

logger = logging.getLogger(__name__)

_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_DEFAULT_MAX_TOKENS = 4096
_ANTHROPIC_BETA_MCP_V1 = "mcp-client-2025-04-04"
_ANTHROPIC_BETA_MCP_V2 = "mcp-client-2025-11-20"

_MCP_ALWAYS_LOADED: frozenset[str] = frozenset(
    {
        "read_project_file",
        "list_project_files",
        "search_project_files",
        "read_file",
        "write_file",
        "edit_file",
        "web_search",
        "web_fetch",
    }
)

_MCP_SERVER_NAME = "gateway-tools"


class AnthropicAdapter:
    def __init__(
        self,
        *,
        config: ProviderConfig,
        client: httpx.AsyncClient,
        event_bus: EventBus | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._event_bus = event_bus
        self._mcp_v2_configured_emitted = False

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

    def _headers(self) -> dict[str, str]:
        headers = {
            "x-api-key": self._config.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        if self._config.mcp_server_url:
            beta = (
                _ANTHROPIC_BETA_MCP_V2
                if self._config.mcp_v2
                else _ANTHROPIC_BETA_MCP_V1
            )
            headers["anthropic-beta"] = beta
        return headers

    @staticmethod
    def _build_mcp_v2_tools(server_name: str) -> list[dict[str, Any]]:
        """Build tool_search_tool + mcp_toolset for defer_loading."""
        return [
            {
                "type": "tool_search_tool_bm25_20251119",
                "name": "tool_search",
            },
            {
                "type": "mcp_toolset",
                "mcp_server_name": server_name,
                "default_config": {"defer_loading": True},
                "configs": {
                    name: {"defer_loading": False}
                    for name in sorted(_MCP_ALWAYS_LOADED)
                },
            },
        ]

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

    @staticmethod
    def _sse_data(payload: dict[str, Any]) -> bytes:
        return f"data: {json.dumps(payload)}\n\n".encode()

    def _openai_to_anthropic(self, request_body: dict[str, Any]) -> dict[str, Any]:
        model_id = str(request_body.get("model", "")).strip()
        anthropic_model = self.to_upstream_model_id(model_id)

        raw_messages = request_body.get("messages")
        openai_messages: list[dict[str, Any]] = (
            raw_messages if isinstance(raw_messages, list) else []
        )

        system_text = extract_system_text(openai_messages)
        anthropic_messages = convert_messages(openai_messages)

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
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
        }
        if system_text:
            payload["system"] = system_text

        for key in ("temperature", "top_p", "top_k"):
            value = request_body.get(key)
            if value is not None:
                payload[key] = value

        stop = request_body.get("stop")
        if isinstance(stop, str):
            payload["stop_sequences"] = [stop]
        elif isinstance(stop, list):
            payload["stop_sequences"] = [s for s in stop if isinstance(s, str)]

        tools_out: list[dict[str, Any]] = []
        tools_in = request_body.get("tools")
        if isinstance(tools_in, list):
            tools_out.extend(
                convert_tools([t for t in tools_in if isinstance(t, dict)])
            )

        native_tool_ids = getattr(self._config, "native_tools", []) or []
        if isinstance(native_tool_ids, list) and native_tool_ids:
            tools_out.extend(
                build_native_tools([t for t in native_tool_ids if isinstance(t, str)])
            )

        tools_out = dedupe_tools(tools_out)

        tool_choice_in = request_body.get("tool_choice")
        tool_choice_out = convert_tool_choice(tool_choice_in)

        if tool_choice_in == "none":
            tools_out = []
            tool_choice_out = None

        if tools_out:
            payload["tools"] = tools_out
        if tool_choice_out is not None and tools_out:
            payload["tool_choice"] = tool_choice_out

        if bool(request_body.get("stream", False)):
            payload["stream"] = True

        if self._config.mcp_server_url:
            payload["mcp_servers"] = [
                {
                    "type": "url",
                    "name": _MCP_SERVER_NAME,
                    "url": self._config.mcp_server_url,
                    "authorization_token": self._config.mcp_auth_token,
                }
            ]
            if self._config.mcp_v2:
                mcp_tools = self._build_mcp_v2_tools(_MCP_SERVER_NAME)
                if "tools" in payload:
                    payload["tools"].extend(mcp_tools)
                else:
                    payload["tools"] = mcp_tools

        return payload

    def _anthropic_to_openai_response(
        self,
        response_json: dict[str, Any],
        requested_model_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        raw_usage = response_json.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        prompt_tokens = self._to_int(usage.get("input_tokens", 0))
        completion_tokens = self._to_int(usage.get("output_tokens", 0))

        stop_reason = response_json.get("stop_reason")
        finish_reason = self._finish_reason(
            stop_reason if isinstance(stop_reason, str) else None
        )

        message, finish_override, citations, mcp_meta = convert_response_content(
            response_json.get("content")
        )
        if finish_override is not None:
            finish_reason = finish_override

        result: dict[str, Any] = {
            "id": response_json.get("id", f"chatcmpl-{int(time.time() * 1000)}"),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": requested_model_id,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

        if citations:
            result["citations"] = citations

        return result, mcp_meta

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
        result, mcp_meta = self._anthropic_to_openai_response(
            response.json(), str(request_body.get("model", ""))
        )
        await self._emit_mcp_response_events(mcp_meta)
        return result

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

            translator = StreamTranslator(str(request_body.get("model", "")))
            async for line in response.aiter_lines():
                chunks = translator.process_line(
                    line,
                    request=response.request,
                    response=response,
                )
                for chunk in chunks:
                    yield chunk
                    if chunk == b"data: [DONE]\n\n":
                        await self._emit_mcp_response_events(translator.mcp_meta)
                        return

            for chunk in translator.finalize():
                yield chunk
            await self._emit_mcp_response_events(translator.mcp_meta)

    async def _emit_mcp_response_events(self, mcp_meta: dict[str, Any]) -> None:
        """Emit per-response MCP events from mcp_meta collected during translation."""
        if not mcp_meta or not self._event_bus:
            return
        for tool_name in mcp_meta.get("mcp_tool_names", []):
            await self._event_bus.publish_async(
                McpAdapterMcpToolUseSeen(
                    tool_name=tool_name,
                    server_name=_MCP_SERVER_NAME,
                )
            )
        ref_count = mcp_meta.get("tool_search_ref_count", 0)
        if ref_count:
            await self._event_bus.publish_async(
                McpAdapterToolSearchSeen(references_count=ref_count)
            )

    async def forward_embeddings(self, request_body: dict[str, Any]) -> dict[str, Any]:
        raise ValueError(
            "Provider 'anthropic' does not support OpenAI embeddings forwarding"
        )
