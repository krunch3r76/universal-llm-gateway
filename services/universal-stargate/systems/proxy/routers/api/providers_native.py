"""Provider-native surfaces — proxy to cloud-proxy /api/v1/providers/...

Generic passthrough: Stargate validates JSON, resolves the cloud-proxy path,
and relays body + response unchanged.  Cloud-proxy owns auth injection,
adapter dispatch, and event publishing.

Prefer ``/v1/chat/completions`` with workspace IDs (``anthropic/...``,
``xai/...``) for OpenAI-compatible clients.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from universal_logging import get_logger

from ..cloud_passthrough import _get_cloud_forwarder

logger = get_logger(__name__)

router = APIRouter(prefix="/providers", tags=["provider-native"])

_VALID_ROUTES: dict[tuple[str, str], str] = {
    ("anthropic", "messages"): "/api/v1/providers/anthropic/messages",
    ("xai", "responses"): "/api/v1/providers/xai/responses",
    ("openai", "responses"): "/api/v1/providers/openai/responses",
}

_CLOUD_UNAVAILABLE = JSONResponse(
    status_code=503,
    content={
        "error": {
            "message": "Cloud proxy not connected",
            "type": "service_unavailable",
            "code": "cloud_proxy_unavailable",
        }
    },
)


async def _read_json_object(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        msg = "Invalid JSON in request body"
        raise ValueError(msg) from exc
    if not isinstance(body, dict):
        msg = "Request body must be a JSON object"
        raise ValueError(msg)
    return body


async def _passthrough(
    request: Request,
    provider: str,
    endpoint: str,
) -> Response:
    """Generic passthrough to cloud-proxy native route."""
    cloud_path = _VALID_ROUTES.get((provider, endpoint))
    if cloud_path is None:
        return JSONResponse(
            status_code=404,
            content={"detail": (f"Unknown provider route: {provider}/{endpoint}")},
        )

    client = _get_cloud_forwarder()
    if not client:
        return _CLOUD_UNAVAILABLE
    try:
        body = await _read_json_object(request)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"detail": str(exc)},
        )

    streaming = bool(body.get("stream", False))
    try:
        if streaming:

            async def _relay() -> AsyncIterator[bytes]:
                async for chunk in client.stream_provider_native(cloud_path, body):
                    yield chunk

            return StreamingResponse(
                _relay(),
                media_type="text/event-stream",
            )

        resp = await client.post_provider_native_json(cloud_path, body)
        media = resp.headers.get("content-type", "application/json")
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=media,
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        preview = (
            exc.response.text[:500] if exc.response is not None else str(exc)[:500]
        )
        logger.warning(
            "Native %s/%s passthrough failed: %s",
            provider,
            endpoint,
            preview,
        )
        return JSONResponse(status_code=status, content={"detail": preview})
    except httpx.HTTPError as exc:
        logger.warning(
            "Native %s/%s transport error: %s",
            provider,
            endpoint,
            exc,
        )
        return JSONResponse(
            status_code=502,
            content={"detail": str(exc)[:300]},
        )


@router.post("/anthropic/messages")
async def native_anthropic_messages(request: Request) -> Response:
    """Native Anthropic Messages (raw model id, native body)."""
    return await _passthrough(request, "anthropic", "messages")


@router.post("/xai/responses")
async def native_xai_responses(request: Request) -> Response:
    """Native xAI Responses (raw model id, native body)."""
    return await _passthrough(request, "xai", "responses")


@router.post("/openai/responses")
async def native_openai_responses(request: Request) -> Response:
    """Native OpenAI Responses (stub in phase 1)."""
    return await _passthrough(request, "openai", "responses")
