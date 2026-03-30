"""Provider-native ingress routes (cloud proxy).

Single shared dispatcher for all provider-native endpoints.  Each route is a
thin wrapper that passes ``provider_key`` and a ``surface`` telemetry tag to
``_forward_native``.  Adapter dispatch, auth injection, and event publishing
are handled once — adding a new provider route requires only a new 3-line
wrapper plus a forwarder entry.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from universal_event_bus import EventBus

from .events import CloudProxyRequestFailed, CloudProxyRequestForwarded
from .forwarder import ProviderForwarder
from .native_boundary import (
    model_id_from_native,
    raw_model_from_native_body,
    workspace_catalog_id_from_native,
)

router = APIRouter(prefix="/api/v1/providers", tags=["provider-native"])


async def _publish_failed(
    event_bus: EventBus | None,
    *,
    provider: str,
    model: str,
    status_code: int,
    error: str,
    adapter_type: str,
) -> None:
    if event_bus is None:
        return
    await event_bus.publish_async(
        CloudProxyRequestFailed(
            provider=provider,
            model=model,
            status_code=status_code,
            error=error,
            adapter_type=adapter_type,
        )
    )


async def _forward_native(
    request: Request,
    *,
    provider_key: str,
    surface: str,
    forwarder: ProviderForwarder,
    event_bus: EventBus | None,
) -> Response:
    """Shared handler for all provider-native routes."""
    from .cloud_proxy import _read_json_object_body

    body = await _read_json_object_body(
        request=request,
        event_bus=event_bus,
        endpoint_name=f"Native {provider_key}",
    )
    raw_model = raw_model_from_native_body(provider_key, body)
    if not raw_model:
        raise HTTPException(
            status_code=400,
            detail="Missing required field: model",
        )

    try:
        _ = model_id_from_native(provider_key, raw_model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    workspace_id = workspace_catalog_id_from_native(provider_key, raw_model)
    streaming = bool(body.get("stream", False))

    try:
        adapter = forwarder.adapter_type(provider_key)
    except ValueError as exc:
        error_text = str(exc)[:300]
        await _publish_failed(
            event_bus,
            provider=provider_key,
            model=workspace_id,
            status_code=503,
            error=error_text,
            adapter_type="unknown",
        )
        raise HTTPException(status_code=503, detail=error_text) from exc

    if streaming:

        async def _stream() -> AsyncIterator[bytes]:
            chunks = forwarder.forward_native_stream(
                provider=provider_key, request_body=body
            )
            async for chunk in chunks:
                yield chunk

        if event_bus:
            await event_bus.publish_async(
                CloudProxyRequestForwarded(
                    provider=provider_key,
                    model=workspace_id,
                    streaming=True,
                    adapter_type=adapter,
                    surface=surface,
                )
            )
        return StreamingResponse(_stream(), media_type="text/event-stream")

    try:
        result = await forwarder.forward_native(
            provider=provider_key, request_body=body
        )
        if event_bus:
            await event_bus.publish_async(
                CloudProxyRequestForwarded(
                    provider=provider_key,
                    model=workspace_id,
                    streaming=False,
                    adapter_type=adapter,
                    surface=surface,
                )
            )
        return JSONResponse(content=result)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        error_text = str(exc)[:300]
        await _publish_failed(
            event_bus,
            provider=provider_key,
            model=workspace_id,
            status_code=status,
            error=error_text,
            adapter_type=adapter,
        )
        raise HTTPException(
            status_code=status,
            detail=f"Upstream provider error: {error_text}",
        ) from exc
    except httpx.HTTPError as exc:
        error_text = str(exc)[:300]
        await _publish_failed(
            event_bus,
            provider=provider_key,
            model=workspace_id,
            status_code=502,
            error=error_text,
            adapter_type=adapter,
        )
        raise HTTPException(status_code=502, detail=error_text) from exc
    except ValueError as exc:
        error_text = str(exc)[:300]
        await _publish_failed(
            event_bus,
            provider=provider_key,
            model=workspace_id,
            status_code=500,
            error=error_text,
            adapter_type=adapter,
        )
        raise HTTPException(status_code=500, detail=error_text) from exc


def _get_forwarder() -> ProviderForwarder:
    from .cloud_proxy import _get_forwarder as _gf

    fwd = _gf()
    assert fwd is not None
    return fwd


def _get_event_bus() -> EventBus | None:
    from .cloud_proxy import _get_event_bus as _ge

    return _ge()


@router.post("/anthropic/messages")
async def native_anthropic_messages(request: Request) -> Response:
    """Anthropic Messages API — native body shape, raw model id."""
    return await _forward_native(
        request,
        provider_key="anthropic",
        surface="anthropic_messages",
        forwarder=_get_forwarder(),
        event_bus=_get_event_bus(),
    )


@router.post("/xai/responses")
async def native_xai_responses(request: Request) -> Response:
    """xAI Responses API — native body shape, raw model id."""
    return await _forward_native(
        request,
        provider_key="xai",
        surface="xai_responses",
        forwarder=_get_forwarder(),
        event_bus=_get_event_bus(),
    )


@router.post("/openai/responses")
async def native_openai_responses_stub() -> JSONResponse:
    """Phase 1 stub; returns 501."""
    return JSONResponse(
        status_code=501,
        content={
            "error": {
                "type": "not_implemented",
                "message": (
                    "OpenAI-native Responses ingress is not "
                    "implemented in phase 1; use "
                    "/v1/chat/completions with workspace model IDs."
                ),
            }
        },
    )
