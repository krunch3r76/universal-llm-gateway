"""Responses API bridge — translate chat/completions to/from Responses API.

Used for OpenAI and xAI ``-mcp`` models on ``/v1/chat/completions``.
These providers support remote MCP on their Responses API but not on chat
completions.  The bridge translates: chat request -> Responses request with
``type: "mcp"`` tool -> Responses response -> chat completion response.

Provider connects to ``mcp.k-1.me:443`` and runs the tool loop on its side.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..config import ProviderConfig

_MCP_SERVER_NAME = "vortex"


def build_mcp_tool_entry(config: ProviderConfig) -> dict[str, Any]:
    """Build a Responses API ``type: "mcp"`` tool entry."""
    tool: dict[str, Any] = {
        "type": "mcp",
        "server_url": str(config.mcp_server_url),
        "server_label": _MCP_SERVER_NAME,
    }
    if config.mcp_auth_token:
        tool["authorization"] = f"Bearer {config.mcp_auth_token}"
    if config.provider.strip().lower() in {"openai", "chatgpt"}:
        tool["require_approval"] = "never"
    return tool


def convert_content_for_responses(
    content: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert chat-completions content parts to Responses API format.

    Chat completions uses ``type: "text"`` / ``type: "image_url"`` with a
    nested ``image_url.url`` field.  Responses API expects
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


def build_responses_body(
    request_body: dict[str, Any],
    config: ProviderConfig,
    upstream_model: str,
) -> dict[str, Any]:
    """Build Responses API body with remote MCP tool from a chat/completions request."""
    input_msgs: list[dict[str, Any]] = []
    for msg in request_body.get("messages", []):
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            msg = {**msg, "content": convert_content_for_responses(content)}
        input_msgs.append(msg)

    mcp_tool = build_mcp_tool_entry(config)
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

    max_tokens = request_body.get("max_tokens") or request_body.get(
        "max_completion_tokens"
    )
    if isinstance(max_tokens, int) and max_tokens > 0:
        body["max_output_tokens"] = max_tokens
    tool_choice = request_body.get("tool_choice")
    if tool_choice is not None and tool_choice != "none":
        body["tool_choice"] = tool_choice
    return body


def responses_to_chat_completion(
    resp_json: dict[str, Any], requested_model: str
) -> dict[str, Any]:
    """Convert Responses API JSON to OpenAI chat completion shape."""
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
            call_id = item.get("call_id") or item.get("id") or f"call_{len(tool_calls)}"
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


async def forward_via_responses(
    client: httpx.AsyncClient,
    config: ProviderConfig,
    request_body: dict[str, Any],
    upstream_model: str,
) -> dict[str, Any]:
    """Non-streaming: POST to /responses and translate back to chat completion."""
    body = build_responses_body(request_body, config, upstream_model)
    requested_model = str(request_body.get("model", ""))
    headers = _provider_headers(config)

    response = await client.post(
        f"{config.base_url}/responses",
        json=body,
        headers=headers,
    )
    if response.status_code >= 400:
        await _raise_provider_http_error(response)
    return responses_to_chat_completion(response.json(), requested_model)


def _sse_frame(
    chunk_id: str,
    created: int,
    model: str,
    delta: dict[str, Any],
    finish_reason: str | None = None,
    usage: dict[str, int] | None = None,
) -> bytes:
    """Encode a single ``chat.completion.chunk`` SSE frame."""
    obj: dict[str, Any] = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        obj["usage"] = usage
    return f"data: {json.dumps(obj)}\n\n".encode()


async def forward_via_responses_stream(
    client: httpx.AsyncClient,
    config: ProviderConfig,
    request_body: dict[str, Any],
    upstream_model: str,
) -> AsyncIterator[bytes]:
    """Streaming: POST to /responses with stream=true, translate SSE events
    from Responses format to ``chat.completion.chunk`` format."""
    body = build_responses_body(request_body, config, upstream_model)
    body["stream"] = True
    requested_model = str(request_body.get("model", ""))
    headers = _provider_headers(config)

    chunk_id = f"chatcmpl-{int(time.time() * 1000)}"
    created = int(time.time())
    sent_role = False
    tool_call_index: dict[str, int] = {}
    has_tool_calls = False

    async with client.stream(
        "POST", f"{config.base_url}/responses", json=body, headers=headers
    ) as response:
        if response.status_code >= 400:
            await _raise_provider_http_error(response)

        async for line in response.aiter_lines():
            stripped = line.strip()
            if (
                not stripped
                or stripped.startswith("event:")
                or not stripped.startswith("data:")
            ):
                continue
            payload = stripped[len("data:") :].strip()
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")

            if etype == "response.output_item.added":
                item = event.get("item", {})
                if isinstance(item, dict) and item.get("type") == "function_call":
                    has_tool_calls = True
                    idx = len(tool_call_index)
                    item_id = str(item.get("id", f"fc_{idx}"))
                    tool_call_index[item_id] = idx
                    call_id = item.get("call_id") or item_id
                    yield _sse_frame(
                        chunk_id,
                        created,
                        requested_model,
                        {
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
                    )
                elif (
                    isinstance(item, dict)
                    and item.get("type") == "message"
                    and item.get("role") == "assistant"
                    and not sent_role
                ):
                    sent_role = True
                    yield _sse_frame(
                        chunk_id, created, requested_model, {"role": "assistant"}
                    )

            elif etype == "response.function_call_arguments.delta":
                idx = tool_call_index.get(str(event.get("item_id", "")))
                if idx is not None:
                    yield _sse_frame(
                        chunk_id,
                        created,
                        requested_model,
                        {
                            "tool_calls": [
                                {
                                    "index": idx,
                                    "function": {"arguments": event.get("delta", "")},
                                }
                            ]
                        },
                    )

            elif etype == "response.output_text.delta":
                delta: dict[str, str] = {"content": event.get("delta", "")}
                if not sent_role:
                    delta["role"] = "assistant"
                    sent_role = True
                yield _sse_frame(chunk_id, created, requested_model, delta)

            elif etype == "response.completed":
                usage_raw = event.get("response", {}).get("usage") or {}
                pt = int(
                    usage_raw.get("input_tokens") or usage_raw.get("prompt_tokens") or 0
                )
                ct = int(
                    usage_raw.get("output_tokens")
                    or usage_raw.get("completion_tokens")
                    or 0
                )
                yield _sse_frame(
                    chunk_id,
                    created,
                    requested_model,
                    {},
                    finish_reason="tool_calls" if has_tool_calls else "stop",
                    usage={
                        "prompt_tokens": pt,
                        "completion_tokens": ct,
                        "total_tokens": pt + ct,
                    },
                )

    yield b"data: [DONE]\n\n"


def _provider_headers(config: ProviderConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }


async def _raise_provider_http_error(response: httpx.Response) -> None:
    error_body = await response.aread()
    error_preview = error_body.decode(errors="replace")[:500]
    raise httpx.HTTPStatusError(
        f"Provider returned {response.status_code}: {error_preview}",
        request=response.request,
        response=response,
    )
