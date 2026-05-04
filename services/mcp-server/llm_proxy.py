"""LLM proxy — multi-provider passthrough with credential injection.

Accepts Anthropic-shaped JSON at ``POST /llm/v1/messages`` (messages, optional
system, max_tokens). Routes by model ID: Anthropic Messages API, or xAI/OpenAI
Responses API. Clients authenticate with the Vortex bearer token; provider keys
never leave the server.

MCP tool calling is NOT injected here.  API calls use client-side tool
resolution via ``frontier_dispatch``.
The Connector pattern (``mcp_servers``) only passes through if the caller
explicitly includes it in the request body.
"""

from __future__ import annotations

import json
import os

import httpx
from llm_adapters import (
    AnthropicAdapter,
    ResponsesAPIAdapter,
    body_to_llm_request,
    effective_provider_for_model,
    resolve_llm_adapter,
)
from mcp_events import monotonic_now, record
from model_id import ModelId
from starlette.requests import Request
from starlette.responses import JSONResponse
from universal_logging import get_logger

logger = get_logger(__name__)

_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_BETA = "mcp-client-2025-11-20"
_ANTHROPIC_BASE = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip(
    "/"
)

_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)


_CORS_HEADERS: dict[str, str] = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "POST, OPTIONS",
    "access-control-allow-headers": "Authorization, Content-Type",
    "access-control-max-age": "86400",
    "vary": "Origin",
}


def handle_llm_preflight() -> JSONResponse:
    """Handle OPTIONS /llm/* — CORS preflight response."""
    return JSONResponse({"status": "ok"}, headers=_CORS_HEADERS)


def _content_block_count(result: dict, *, provider: str) -> int:
    if provider == "anthropic":
        return len(result.get("content", []))
    return len(result.get("output", []))


async def handle_llm_proxy(request: Request) -> JSONResponse:
    """Proxy POST /llm/v1/messages to the resolved provider with key injection."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(
            {"error": "Invalid JSON"},
            status_code=400,
            headers=_CORS_HEADERS,
        )

    if not isinstance(body, dict):
        return JSONResponse(
            {"error": "Request body must be a JSON object"},
            status_code=400,
            headers=_CORS_HEADERS,
        )

    if "messages" not in body:
        return JSONResponse(
            {"error": "messages field is required"},
            status_code=400,
            headers=_CORS_HEADERS,
        )

    raw_model = body.get("model")
    if not raw_model:
        return JSONResponse(
            {"error": "model field is required"},
            status_code=400,
            headers=_CORS_HEADERS,
        )
    parsed = ModelId.parse(str(raw_model))
    routing = parsed.routing_layer

    if routing == "openrouter":
        return JSONResponse(
            {"error": "OpenRouter routing not yet implemented via LLM proxy"},
            status_code=501,
            headers=_CORS_HEADERS,
        )

    if parsed.provider is None:
        return JSONResponse(
            {"error": "Cloud provider model_id is required (provider/model)"},
            status_code=400,
            headers=_CORS_HEADERS,
        )

    provider_key = effective_provider_for_model(parsed.provider)
    adapter = resolve_llm_adapter(parsed.provider)
    if adapter is None:
        logger.error(
            "No API key for LLM provider %s — LLM proxy unavailable", provider_key
        )
        return JSONResponse(
            {
                "error": f"LLM proxy not configured (missing API key for provider {provider_key})",
            },
            status_code=503,
            headers=_CORS_HEADERS,
        )

    model = parsed.api_model_id
    body["model"] = model
    t0 = monotonic_now()
    record("mcp.llm.proxy.called", model=model, provider=provider_key)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            if isinstance(adapter, AnthropicAdapter):
                upstream_headers = {
                    "x-api-key": os.environ.get("ANTHROPIC_API_KEY", "").strip(),
                    "anthropic-version": _ANTHROPIC_VERSION,
                    "content-type": "application/json",
                }
                if body.get("mcp_servers"):
                    upstream_headers["anthropic-beta"] = _ANTHROPIC_BETA
                body.pop("stream", None)
                resp = await client.post(
                    f"{_ANTHROPIC_BASE}/v1/messages",
                    headers=upstream_headers,
                    json=body,
                )
            elif isinstance(adapter, ResponsesAPIAdapter):
                llm_req = body_to_llm_request(body, model)
                url, headers, json_body = adapter.build_request(llm_req)
                resp = await client.post(url, headers=headers, json=json_body)
            else:
                return JSONResponse(
                    {"error": "Unsupported adapter type"},
                    status_code=500,
                    headers=_CORS_HEADERS,
                )
    except httpx.TimeoutException:
        duration = monotonic_now() - t0
        record(
            "mcp.llm.proxy.timeout",
            duration_s=round(duration, 3),
            model=model,
            provider=provider_key,
        )
        logger.warning(
            "LLM upstream timeout after %.1fs (provider=%s)", duration, provider_key
        )
        return JSONResponse(
            {"error": "Upstream timeout"},
            status_code=504,
            headers=_CORS_HEADERS,
        )
    except httpx.RequestError as exc:
        duration = monotonic_now() - t0
        record(
            "mcp.llm.proxy.error",
            duration_s=round(duration, 3),
            error=str(exc),
            provider=provider_key,
        )
        logger.error("LLM upstream request failed: %s", exc)
        return JSONResponse(
            {"error": "Upstream connection failed"},
            status_code=502,
            headers=_CORS_HEADERS,
        )

    duration = monotonic_now() - t0

    if resp.status_code >= 400:
        record(
            "mcp.llm.proxy.upstream.error",
            status=resp.status_code,
            duration_s=round(duration, 3),
            model=model,
            provider=provider_key,
        )
        logger.warning(
            "LLM upstream returned %d after %.1fs (provider=%s)",
            resp.status_code,
            duration,
            provider_key,
        )
        return JSONResponse(
            {
                "error": f"Upstream error ({resp.status_code})",
                "detail": resp.text[:500],
            },
            status_code=502,
            headers=_CORS_HEADERS,
        )

    try:
        result = resp.json()
    except json.JSONDecodeError:
        record(
            "mcp.llm.proxy.invalid.response",
            duration_s=round(duration, 3),
            provider=provider_key,
        )
        return JSONResponse(
            {"error": "Upstream returned invalid JSON"},
            status_code=502,
            headers=_CORS_HEADERS,
        )

    if not isinstance(result, dict):
        record("mcp.llm.proxy.invalid.response", duration_s=round(duration, 3))
        return JSONResponse(
            {"error": "Upstream returned non-object JSON"},
            status_code=502,
            headers=_CORS_HEADERS,
        )

    blocks = _content_block_count(result, provider=provider_key)
    record(
        "mcp.llm.proxy.completed",
        duration_s=round(duration, 3),
        content_blocks=blocks,
        model=result.get("model", model),
        provider=provider_key,
    )
    logger.info(
        "LLM proxy completed: %.1fs, %d blocks, model=%s provider=%s",
        duration,
        blocks,
        model,
        provider_key,
    )

    return JSONResponse(result, headers=_CORS_HEADERS)
