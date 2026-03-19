"""LLM proxy — Anthropic Messages API passthrough with credential + MCP injection.

Accepts standard Anthropic Messages API requests at ``/llm/v1/messages``,
injects the Anthropic API key and MCP server configuration server-side,
then forwards to ``https://api.anthropic.com/v1/messages``.

Clients authenticate with a Vortex bearer token.  The Anthropic key and
MCP server URL never leave the server.
"""

from __future__ import annotations

import json
import os

import httpx
from mcp_events import monotonic_now, record
from model_id import ModelId
from starlette.requests import Request
from starlette.responses import JSONResponse
from universal_logging import get_logger

logger = get_logger(__name__)

_ANTHROPIC_API_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_BETA = "mcp-client-2025-11-20"
_MCP_SERVER_NAME = "vortex"
_MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "").strip()

_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)


_CORS_HEADERS: dict[str, str] = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "POST, OPTIONS",
    "access-control-allow-headers": "Authorization, Content-Type",
    "access-control-max-age": "86400",
    "vary": "Origin",
}


def _get_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY", "").strip() or None


def _get_mcp_auth_token() -> str:
    return os.environ.get("MCP_AUTH_TOKEN", "").strip()


def handle_llm_preflight() -> JSONResponse:
    """Handle OPTIONS /llm/* — CORS preflight response."""
    return JSONResponse({"status": "ok"}, headers=_CORS_HEADERS)


async def handle_llm_proxy(request: Request) -> JSONResponse:
    """Proxy POST /llm/v1/messages to Anthropic with key + MCP injection.

    The client sends a standard Anthropic Messages API payload.  This handler:
    1. Validates the JSON body
    2. Injects ``mcp_servers`` (if configured and tools not suppressed)
    3. Forwards to Anthropic with server-side ``x-api-key``
    4. Returns the upstream response unchanged
    """
    api_key = _get_api_key()
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set — LLM proxy unavailable")
        return JSONResponse(
            {"error": "LLM proxy not configured (missing API key)"},
            status_code=503,
            headers=_CORS_HEADERS,
        )

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

    if _MCP_SERVER_URL and body.get("tool_choice") != "none":
        mcp_auth = _get_mcp_auth_token()
        mcp_server_def: dict[str, str] = {
            "type": "url",
            "url": _MCP_SERVER_URL,
            "name": _MCP_SERVER_NAME,
        }
        if mcp_auth:
            mcp_server_def["authorization_token"] = mcp_auth

        if "mcp_servers" not in body:
            body["mcp_servers"] = [mcp_server_def]

        if "tools" not in body:
            body["tools"] = [
                {"type": "mcp_toolset", "mcp_server_name": _MCP_SERVER_NAME}
            ]

    raw_model = body.get("model", "unknown")
    parsed = ModelId.parse(raw_model)
    routing = parsed.routing_layer or "native"

    if routing == "openrouter":
        return JSONResponse(
            {"error": "OpenRouter routing not yet implemented via LLM proxy"},
            status_code=501,
            headers=_CORS_HEADERS,
        )

    model = parsed.api_model_id
    body["model"] = model
    t0 = monotonic_now()
    record("mcp.llm.proxy.called", model=model)

    upstream_headers = {
        "x-api-key": api_key,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    if body.get("mcp_servers"):
        upstream_headers["anthropic-beta"] = _ANTHROPIC_BETA

    body.pop("stream", None)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_ANTHROPIC_API_URL}/v1/messages",
                headers=upstream_headers,
                json=body,
            )
    except httpx.TimeoutException:
        duration = monotonic_now() - t0
        record("mcp.llm.proxy.timeout", duration_s=round(duration, 3), model=model)
        logger.warning("Anthropic API timeout after %.1fs", duration)
        return JSONResponse(
            {"error": "Upstream timeout"},
            status_code=504,
            headers=_CORS_HEADERS,
        )
    except httpx.RequestError as exc:
        duration = monotonic_now() - t0
        record("mcp.llm.proxy.error", duration_s=round(duration, 3), error=str(exc))
        logger.error("Anthropic API request failed: %s", exc)
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
        )
        logger.warning(
            "Anthropic API returned %d after %.1fs",
            resp.status_code,
            duration,
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
        record("mcp.llm.proxy.invalid.response", duration_s=round(duration, 3))
        return JSONResponse(
            {"error": "Upstream returned invalid JSON"},
            status_code=502,
            headers=_CORS_HEADERS,
        )

    content_blocks = len(result.get("content", []))
    record(
        "mcp.llm.proxy.completed",
        duration_s=round(duration, 3),
        content_blocks=content_blocks,
        model=result.get("model", model),
    )
    logger.info(
        "LLM proxy completed: %.1fs, %d blocks, model=%s",
        duration,
        content_blocks,
        model,
    )

    return JSONResponse(result, headers=_CORS_HEADERS)
