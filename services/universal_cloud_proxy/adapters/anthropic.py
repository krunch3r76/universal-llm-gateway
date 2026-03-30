from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
import uuid
from typing import TYPE_CHECKING, Any

import httpx
from model_id import ModelId

from ..events import (
    CloudproxyMcpCorrelationAssigned,
    CloudproxyMcpPathFailed,
    CloudproxyMcpRequestCompleted,
    CloudproxyMcpRequestStarted,
    CloudproxyMcpStreamCancelled,
    CloudproxyMcpStreamHeartbeat,
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

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from universal_event_bus import EventBus

    from ..config import ProviderConfig

logger = logging.getLogger(__name__)

_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_BETA_MCP_V1 = "mcp-client-2025-04-04"
_ANTHROPIC_BETA_MCP_V2 = "mcp-client-2025-11-20"
_STREAM_HEARTBEAT_INTERVAL_S = float(
    os.getenv("CLOUDPROXY_ANTHROPIC_STREAM_HEARTBEAT_S", "15")
)

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

_MCP_SERVER_NAME = "vortex"


class AnthropicAdapter:
    """Translate OpenAI-compatible requests to Anthropic APIs and back."""

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
        """Normalize provider model IDs into the catalog namespace.

        Bare model names get anthropic/ prefix. Slashful IDs pass through.
        """
        if "/" in raw_model_id:
            return raw_model_id
        return f"anthropic/{raw_model_id}"

    def to_upstream_model_id(self, catalog_model_id: str) -> str:
        """Strip anthropic/ prefix and ``-mcp`` — Anthropic API expects bare model names."""
        return ModelId.parse(catalog_model_id).api_model_id

    def _headers(self, *, include_mcp_beta: bool = False) -> dict[str, str]:
        """Build HTTP headers for Anthropic API requests."""
        headers = {
            "x-api-key": self._config.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        if include_mcp_beta and self._config.mcp_server_url:
            headers["anthropic-beta"] = (
                _ANTHROPIC_BETA_MCP_V2
                if self._config.mcp_v2
                else _ANTHROPIC_BETA_MCP_V1
            )
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
        """Map Anthropic stop reasons to OpenAI-compatible finish reasons."""
        mapping = {
            "end_turn": "stop",
            "stop_sequence": "stop",
            "max_tokens": "length",
            "tool_use": "tool_calls",
        }
        return mapping.get(stop_reason, stop_reason)

    @staticmethod
    def _to_int(value: Any) -> int:
        """Coerce numeric values from provider payloads to integers."""
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            logger.warning("Failed to coerce int from value=%r: %s", value, exc)
            return 0

    @staticmethod
    def _sse_data(payload: dict[str, Any]) -> bytes:
        """Encode an SSE data frame from a JSON payload."""
        return f"data: {json.dumps(payload)}\n\n".encode()

    @staticmethod
    def _sse_comment(comment: str) -> bytes:
        """Encode an SSE comment frame that clients ignore but proxies observe."""
        return f": {comment}\n\n".encode()

    def _openai_to_anthropic(self, request_body: dict[str, Any]) -> dict[str, Any]:
        """Convert OpenAI-compatible request payloads into Anthropic format."""
        model_id = str(request_body.get("model", "")).strip()
        parsed = ModelId.parse(model_id)
        anthropic_model = parsed.api_model_id

        raw_messages = request_body.get("messages")
        openai_messages: list[dict[str, Any]] = (
            raw_messages if isinstance(raw_messages, list) else []
        )

        system_text = extract_system_text(openai_messages)
        anthropic_messages = convert_messages(openai_messages)

        response_format = request_body.get("response_format")
        json_mode = False
        json_schema: dict[str, Any] | None = None

        if isinstance(response_format, dict):
            rf_type = response_format.get("type")
            if rf_type == "json_object":
                json_mode = True
            elif rf_type == "json_schema":
                json_mode = True
                json_schema = response_format.get("json_schema")

        if json_mode:
            json_instruction = (
                "Respond with valid JSON only. "
                "Do not wrap in markdown code fences. "
                "Do not include any text outside the JSON object."
            )
            if isinstance(json_schema, dict):
                schema_obj = json_schema.get("schema")
                if isinstance(schema_obj, dict):
                    json_instruction += (
                        f" The response must conform to this JSON schema: "
                        f"{json.dumps(schema_obj)}"
                    )
            system_text = (
                f"{system_text}\n\n{json_instruction}".strip()
                if system_text
                else json_instruction
            )

        max_tokens = request_body.get("max_tokens")
        if not isinstance(max_tokens, int):
            max_tokens_from_completion = request_body.get("max_completion_tokens")
            if isinstance(max_tokens_from_completion, int):
                max_tokens = max_tokens_from_completion
            elif self._config.default_max_tokens is not None:
                max_tokens = self._config.default_max_tokens

        payload: dict[str, Any] = {
            "model": anthropic_model,
            "messages": anthropic_messages,
        }
        if isinstance(max_tokens, int):
            payload["max_tokens"] = max_tokens
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

        native_tool_ids = getattr(self._config, "native_tools", [])
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

        # Remote MCP only when model id ends with -mcp and provider has MCP URL.
        inject_mcp = (
            self._config.mcp_server_url and parsed.is_mcp and tool_choice_in != "none"
        )
        if inject_mcp:
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
                payload.setdefault("tools", []).extend(mcp_tools)

        return payload

    def _anthropic_to_openai_response(
        self,
        response_json: dict[str, Any],
        requested_model_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Convert a non-streaming Anthropic response to OpenAI shape."""
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
        """Fetch model catalog entries from the Anthropic endpoint."""
        response = await self._client.get(
            f"{self._config.base_url}/models",
            headers=self._headers(),
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        data = body.get("data", [])
        return data if isinstance(data, list) else []

    async def _raise_provider_http_error(self, response: httpx.Response) -> None:
        """Raise HTTPStatusError with provider response body preserved for diagnostics."""
        error_preview = response.text[:500]
        logger.error(
            "Anthropic API %d: %s",
            response.status_code,
            error_preview,
        )
        raise httpx.HTTPStatusError(
            f"Provider returned {response.status_code}: {error_preview}",
            request=response.request,
            response=response,
        )

    async def forward_chat(self, request_body: dict[str, Any]) -> dict[str, Any]:
        """Forward a non-streaming Anthropic request, preserving provider error detail and normalizing the response."""
        correlation_id = str(uuid.uuid4())
        t0 = time.monotonic()
        outcome = "error"
        try:
            body = self._openai_to_anthropic(request_body)
            has_mcp = bool(body.get("mcp_servers"))
            req_headers = dict(self._headers(include_mcp_beta=has_mcp))
            req_headers["X-Cloudproxy-Correlation-Id"] = correlation_id
            if self._event_bus:
                await self._event_bus.publish_async(
                    CloudproxyMcpCorrelationAssigned(
                        correlation_id=correlation_id,
                        provider=self._config.provider,
                    )
                )
                await self._event_bus.publish_async(
                    CloudproxyMcpRequestStarted(
                        correlation_id=correlation_id,
                        provider=self._config.provider,
                        model=str(request_body.get("model", "")),
                        has_mcp_servers=has_mcp,
                        streaming=False,
                    )
                )
            response = await self._client.post(
                f"{self._config.base_url}/messages",
                json=body,
                headers=req_headers,
            )
            if response.status_code >= 400:
                await self._raise_provider_http_error(response)
            result, mcp_meta = self._anthropic_to_openai_response(
                response.json(), str(request_body.get("model", ""))
            )
            await self._emit_mcp_response_events(
                mcp_meta, correlation_id=correlation_id
            )
            outcome = "success"
            return result
        except Exception as exc:
            if self._event_bus:
                await self._event_bus.publish_async(
                    CloudproxyMcpPathFailed(
                        correlation_id=correlation_id,
                        provider=self._config.provider,
                        error=str(exc)[:500],
                        exc_type=type(exc).__name__,
                    )
                )
            raise
        finally:
            if self._event_bus:
                duration = time.monotonic() - t0
                await self._event_bus.publish_async(
                    CloudproxyMcpRequestCompleted(
                        correlation_id=correlation_id,
                        provider=self._config.provider,
                        duration_s=round(duration, 3),
                        outcome=outcome,
                    )
                )

    async def forward_chat_stream(
        self, request_body: dict[str, Any]
    ) -> AsyncIterator[bytes]:
        """Forward streaming chat requests as OpenAI-compatible SSE chunks."""
        correlation_id = str(uuid.uuid4())
        t0 = time.monotonic()
        outcome = "error"
        requested_model = str(request_body.get("model", ""))

        async def _gen() -> AsyncIterator[bytes]:
            nonlocal outcome
            stage = "build_request"
            try:
                body = self._openai_to_anthropic({**request_body, "stream": True})
                has_mcp = bool(body.get("mcp_servers"))
                req_headers = dict(self._headers(include_mcp_beta=has_mcp))
                req_headers["X-Cloudproxy-Correlation-Id"] = correlation_id
                translator = StreamTranslator(requested_model)
                if self._event_bus:
                    await self._event_bus.publish_async(
                        CloudproxyMcpCorrelationAssigned(
                            correlation_id=correlation_id,
                            provider=self._config.provider,
                        )
                    )
                    await self._event_bus.publish_async(
                        CloudproxyMcpRequestStarted(
                            correlation_id=correlation_id,
                            provider=self._config.provider,
                            model=str(request_body.get("model", "")),
                            has_mcp_servers=has_mcp,
                            streaming=True,
                        )
                    )
                stage = "open_upstream_stream"
                async with self._client.stream(
                    "POST",
                    f"{self._config.base_url}/messages",
                    json=body,
                    headers=req_headers,
                ) as response:
                    if response.status_code >= 400:
                        error_body = await response.aread()
                        error_preview = error_body.decode(errors="replace")[:300]
                        raise httpx.HTTPStatusError(
                            f"Provider returned {response.status_code}: {error_preview}",
                            request=response.request,
                            response=response,
                        )

                    stage = "await_upstream_chunk"
                    done_seen = False
                    heartbeat_enabled = _STREAM_HEARTBEAT_INTERVAL_S > 0
                    line_iter = response.aiter_lines()
                    pending_line: asyncio.Task[str] | None = asyncio.create_task(
                        anext(line_iter)
                    )
                    try:
                        while pending_line is not None:
                            done, _ = await asyncio.wait(
                                {pending_line},
                                timeout=(
                                    _STREAM_HEARTBEAT_INTERVAL_S
                                    if heartbeat_enabled
                                    else None
                                ),
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            if not done:
                                if self._event_bus:
                                    await self._event_bus.publish_async(
                                        CloudproxyMcpStreamHeartbeat(
                                            correlation_id=correlation_id,
                                            provider=self._config.provider,
                                            model=requested_model,
                                            idle_s=_STREAM_HEARTBEAT_INTERVAL_S,
                                        )
                                    )
                                yield self._sse_comment("heartbeat")
                                continue

                            try:
                                line = pending_line.result()
                            except StopAsyncIteration:
                                pending_line = None
                                break
                            pending_line = None
                            stage = "relay_upstream_chunk"
                            chunks = translator.process_line(
                                line,
                                request=response.request,
                                response=response,
                            )
                            for chunk in chunks:
                                if chunk == b"data: [DONE]\n\n":
                                    if not done_seen:
                                        yield chunk
                                        done_seen = True
                                    continue  # Skip subsequent [DONE] from process_line
                                yield chunk
                            stage = "await_upstream_chunk"
                            pending_line = asyncio.create_task(anext(line_iter))
                    finally:
                        if pending_line is not None and not pending_line.done():
                            pending_line.cancel()
                            with contextlib.suppress(
                                asyncio.CancelledError, StopAsyncIteration
                            ):
                                await pending_line

                    stage = "finalize_stream"
                    for chunk in translator.finalize():
                        if chunk == b"data: [DONE]\n\n":
                            if not done_seen:
                                yield chunk
                                done_seen = True
                        else:
                            yield chunk
                    await self._emit_mcp_response_events(
                        translator.mcp_meta, correlation_id=correlation_id
                    )
                    outcome = "success"
            except asyncio.CancelledError:
                outcome = "cancelled"
                if self._event_bus:
                    await self._event_bus.publish_async(
                        CloudproxyMcpStreamCancelled(
                            correlation_id=correlation_id,
                            provider=self._config.provider,
                            model=requested_model,
                            duration_s=round(time.monotonic() - t0, 3),
                            stage=stage,
                            reason="downstream_cancelled",
                        )
                    )
                raise
            except Exception as exc:
                if self._event_bus:
                    await self._event_bus.publish_async(
                        CloudproxyMcpPathFailed(
                            correlation_id=correlation_id,
                            provider=self._config.provider,
                            error=str(exc)[:500],
                            exc_type=type(exc).__name__,
                        )
                    )
                raise
            finally:
                if self._event_bus:
                    duration = time.monotonic() - t0
                    await self._event_bus.publish_async(
                        CloudproxyMcpRequestCompleted(
                            correlation_id=correlation_id,
                            provider=self._config.provider,
                            duration_s=round(duration, 3),
                            outcome=outcome,
                        )
                    )

        async for chunk in _gen():
            yield chunk

    async def _emit_mcp_response_events(
        self,
        mcp_meta: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> None:
        """Emit per-response MCP events from mcp_meta collected during translation."""
        if not mcp_meta or not self._event_bus:
            return
        for tool_name in mcp_meta.get("mcp_tool_names", []):
            await self._event_bus.publish_async(
                McpAdapterMcpToolUseSeen(
                    tool_name=tool_name,
                    server_name=_MCP_SERVER_NAME,
                    correlation_id=correlation_id,
                )
            )
        ref_count = mcp_meta.get("tool_search_ref_count", 0)
        if ref_count:
            await self._event_bus.publish_async(
                McpAdapterToolSearchSeen(
                    references_count=ref_count,
                    correlation_id=correlation_id,
                )
            )

    async def forward_embeddings(self, request_body: dict[str, Any]) -> dict[str, Any]:
        """Reject embeddings because Anthropic does not expose that API."""
        raise ValueError(
            "Provider 'anthropic' does not support OpenAI embeddings forwarding"
        )
