"""Workbench relay — server-side proxy to Anthropic Messages API with MCP tools.

Accepts ``{system, user_msg, max_tokens}`` from the Cortex Workbench artifact,
calls Anthropic with the Vortex MCP server attached, and returns the full
response.  Keeps the Anthropic API key and ``mcp_servers`` config server-side
so the browser artifact never needs direct API access or MCP connector auth.
"""

from __future__ import annotations

import json
import logging
import os

import httpx
from mcp_events import monotonic_now, record
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

_ANTHROPIC_API_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_BETA = "mcp-client-2025-11-20"
_DEFAULT_MODEL = "claude-sonnet-4-20250514"
_MAX_TOKENS_CAP = 16384
_MCP_SERVER_NAME = "vortex"
_MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "").strip()
_ALLOWED_ORIGIN = "*"

_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)

_CORS_HEADERS: dict[str, str] = {
    "access-control-allow-origin": _ALLOWED_ORIGIN,
    "access-control-allow-methods": "POST, OPTIONS",
    "access-control-allow-headers": "Authorization, Content-Type",
    "access-control-max-age": "86400",
    "vary": "Origin",
}


def _get_api_key() -> str | None:
    """Resolve the Anthropic API key from environment."""
    return os.environ.get("ANTHROPIC_API_KEY", "").strip() or None


def _get_mcp_auth_token() -> str:
    """Resolve the MCP auth token for Anthropic's MCP connector to call back into Vortex."""
    return os.environ.get("MCP_AUTH_TOKEN", "").strip()


async def handle_relay(request: Request) -> JSONResponse:
    """Handle POST /workbench/relay — proxy to Anthropic Messages API.

    Validates the JSON payload, calls Anthropic with ``mcp_servers`` attached,
    and returns the response unchanged.

    Args:
        request: Starlette request (auth already validated by middleware).

    Returns:
        JSONResponse with the Anthropic API response or an error envelope.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set — relay unavailable")
        return JSONResponse(
            {"error": "Relay not configured (missing API key)"},
            status_code=503,
            headers=_CORS_HEADERS,
        )

    if not _MCP_SERVER_URL:
        logger.error("MCP_SERVER_URL not set — relay unavailable")
        return JSONResponse(
            {"error": "Relay not configured (missing MCP server URL)"},
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

    system = body.get("system", "")
    user_msg = body.get("user_msg", "")
    max_tokens = body.get("max_tokens", 4096)

    if not user_msg or not isinstance(user_msg, str):
        return JSONResponse(
            {"error": "user_msg is required and must be a non-empty string"},
            status_code=400,
            headers=_CORS_HEADERS,
        )

    if not isinstance(max_tokens, int) or max_tokens < 1:
        max_tokens = 4096
    max_tokens = min(max_tokens, _MAX_TOKENS_CAP)

    mcp_auth = _get_mcp_auth_token()
    mcp_server_def: dict = {
        "type": "url",
        "url": _MCP_SERVER_URL,
        "name": _MCP_SERVER_NAME,
    }
    if mcp_auth:
        mcp_server_def["authorization_token"] = mcp_auth

    anthropic_payload: dict = {
        "model": _DEFAULT_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user_msg}],
        "mcp_servers": [mcp_server_def],
        "tools": [{"type": "mcp_toolset", "mcp_server_name": _MCP_SERVER_NAME}],
    }
    if system:
        anthropic_payload["system"] = system

    t0 = monotonic_now()
    record(
        "mcp.workbench.relay.called",
        model=_DEFAULT_MODEL,
        max_tokens=max_tokens,
        user_msg_len=len(user_msg),
    )

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_ANTHROPIC_API_URL}/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": _ANTHROPIC_VERSION,
                    "anthropic-beta": _ANTHROPIC_BETA,
                    "content-type": "application/json",
                },
                json=anthropic_payload,
            )
    except httpx.TimeoutException:
        duration = monotonic_now() - t0
        record(
            "mcp.workbench.relay.timeout",
            duration_s=round(duration, 3),
        )
        logger.warning("Anthropic API timeout after %.1fs", duration)
        return JSONResponse(
            {"error": "Upstream timeout"},
            status_code=504,
            headers=_CORS_HEADERS,
        )
    except httpx.RequestError as exc:
        duration = monotonic_now() - t0
        record(
            "mcp.workbench.relay.error",
            duration_s=round(duration, 3),
            error=str(exc),
        )
        logger.error("Anthropic API request failed: %s", exc)
        return JSONResponse(
            {"error": "Upstream connection failed"},
            status_code=502,
            headers=_CORS_HEADERS,
        )

    duration = monotonic_now() - t0

    if resp.status_code >= 400:
        record(
            "mcp.workbench.relay.upstream.error",
            status=resp.status_code,
            duration_s=round(duration, 3),
        )
        logger.warning(
            "Anthropic API returned %d after %.1fs",
            resp.status_code,
            duration,
        )
        relay_status = 504 if resp.status_code == 408 else 502
        return JSONResponse(
            {"error": f"Upstream error ({resp.status_code})"},
            status_code=relay_status,
            headers=_CORS_HEADERS,
        )

    try:
        result = resp.json()
    except json.JSONDecodeError:
        record(
            "mcp.workbench.relay.invalid.response",
            duration_s=round(duration, 3),
        )
        return JSONResponse(
            {"error": "Upstream returned invalid JSON"},
            status_code=502,
            headers=_CORS_HEADERS,
        )

    content_blocks = len(result.get("content", []))
    record(
        "mcp.workbench.relay.completed",
        duration_s=round(duration, 3),
        content_blocks=content_blocks,
        model=result.get("model", _DEFAULT_MODEL),
    )
    logger.info("Relay completed: %.1fs, %d content blocks", duration, content_blocks)

    return JSONResponse(result, headers=_CORS_HEADERS)


def handle_preflight() -> JSONResponse:
    """Handle OPTIONS /workbench/relay — CORS preflight response."""
    return JSONResponse({"status": "ok"}, headers=_CORS_HEADERS)
