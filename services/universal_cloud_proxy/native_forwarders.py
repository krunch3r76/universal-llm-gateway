"""Provider-native forwarders for cloud-proxy ingress."""

from __future__ import annotations

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from universal_event_bus import EventBus
from universal_logging import get_logger

from .events import CloudProxyRequestForwarded
from .forwarder import ProviderForwarder
from .native_boundary import (
    _EFFORT_SUFFIX,
    model_id_from_native,
    raw_model_from_native_body,
    workspace_catalog_id_from_native,
)
from .native_forward_errors import publish_and_raise, publish_failed
from .native_mcp_inject import get_mcp_config_for_provider, inject_native_mcp
from .native_streaming import preflight_native_byte_stream

logger = get_logger(__name__)


async def assert_catalog_model_known(
    *,
    event_bus: EventBus | None,
    provider_key: str,
    workspace_id: str,
) -> None:
    """Reject unknown model IDs before upstream dispatch (cached catalog only)."""
    from .cloud_proxy import _get_catalog

    catalog = _get_catalog()
    if catalog is None or catalog.model_known(workspace_id):
        return
    detail = (
        f"Model not found in cloud catalog: {workspace_id}. "
        "Check GET /catalog or gateway /v1/models for available IDs."
    )
    await publish_failed(
        event_bus,
        provider=provider_key,
        model=workspace_id,
        status_code=404,
        error=detail,
        adapter_type="unknown",
    )
    raise HTTPException(status_code=404, detail=detail)


async def forward_native(
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

    mcp_requested = raw_model.endswith("-mcp")
    if mcp_requested:
        raw_model = raw_model[:-4]
        body = {**body, "model": raw_model}
        inject_native_mcp(provider_key, body)

    _xai_injected_effort: str | None = None
    if provider_key == "xai" and _EFFORT_SUFFIX in raw_model:
        raw_model, _xai_injected_effort = raw_model.split(_EFFORT_SUFFIX, 1)
        body = {**body, "model": raw_model}

    try:
        _ = model_id_from_native(provider_key, raw_model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    workspace_id = workspace_catalog_id_from_native(provider_key, raw_model)
    streaming = bool(body.get("stream", False))

    await assert_catalog_model_known(
        event_bus=event_bus,
        provider_key=provider_key,
        workspace_id=workspace_id,
    )

    if _xai_injected_effort is not None:
        existing_reasoning = body.get("reasoning") or {}
        if "effort" not in existing_reasoning:
            body = {
                **body,
                "reasoning": {**existing_reasoning, "effort": _xai_injected_effort},
            }
            logger.info(
                "xAI reasoning.effort injected from model suffix — model=%s effort=%s",
                raw_model,
                _xai_injected_effort,
            )

    try:
        adapter = forwarder.adapter_type(provider_key)
    except ValueError as exc:
        await publish_and_raise(
            event_bus,
            exc=exc,
            provider=provider_key,
            model=workspace_id,
            adapter_type="unknown",
        )

    if streaming:
        chunks = forwarder.forward_native_stream(
            provider=provider_key, request_body=body
        )
        try:
            primed = await preflight_native_byte_stream(chunks)
        except (httpx.HTTPStatusError, httpx.HTTPError, ValueError) as exc:
            await publish_and_raise(
                event_bus,
                exc=exc,
                provider=provider_key,
                model=workspace_id,
                adapter_type=adapter,
            )

        if event_bus:
            await event_bus.publish_nowait(
                CloudProxyRequestForwarded(
                    provider=provider_key,
                    model=workspace_id,
                    streaming=True,
                    adapter_type=adapter,
                    surface=surface,
                )
            )
        return StreamingResponse(primed, media_type="text/event-stream")

    try:
        result = await forwarder.forward_native(
            provider=provider_key, request_body=body
        )
        if event_bus:
            await event_bus.publish_nowait(
                CloudProxyRequestForwarded(
                    provider=provider_key,
                    model=workspace_id,
                    streaming=False,
                    adapter_type=adapter,
                    surface=surface,
                )
            )
        return JSONResponse(content=result)
    except (httpx.HTTPStatusError, httpx.HTTPError, ValueError) as exc:
        await publish_and_raise(
            event_bus,
            exc=exc,
            provider=provider_key,
            model=workspace_id,
            adapter_type=adapter,
        )


async def forward_images(
    request: Request,
    *,
    provider_key: str,
    image_endpoint: str,
    surface: str,
    forwarder: ProviderForwarder,
    event_bus: EventBus | None,
) -> Response:
    """Shared handler for image generation/editing routes."""
    from .cloud_proxy import _read_json_object_body

    body = await _read_json_object_body(
        request=request,
        event_bus=event_bus,
        endpoint_name=f"Image {image_endpoint} {provider_key}",
    )
    raw_model = raw_model_from_native_body(provider_key, body)
    if not raw_model:
        raise HTTPException(status_code=400, detail="Missing required field: model")

    try:
        _ = model_id_from_native(provider_key, raw_model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    workspace_id = workspace_catalog_id_from_native(provider_key, raw_model)
    adapter = forwarder.adapter_type(provider_key)

    try:
        if image_endpoint == "edits":
            result = await forwarder.forward_image_edit(
                provider=provider_key, request_body=body
            )
        else:
            result = await forwarder.forward_image_generation(
                provider=provider_key, request_body=body
            )
        if event_bus:
            await event_bus.publish_nowait(
                CloudProxyRequestForwarded(
                    provider=provider_key,
                    model=workspace_id,
                    streaming=False,
                    adapter_type=adapter,
                    surface=surface,
                )
            )
        return JSONResponse(content=result)
    except (httpx.HTTPStatusError, httpx.HTTPError, ValueError) as exc:
        await publish_and_raise(
            event_bus,
            exc=exc,
            provider=provider_key,
            model=workspace_id,
            adapter_type=adapter,
        )


async def forward_video_generation(
    request: Request,
    *,
    provider_key: str,
    forwarder: ProviderForwarder,
    event_bus: EventBus | None,
) -> Response:
    """POST /videos/generations — submit async video job, return request_id."""
    from .cloud_proxy import _read_json_object_body

    body = await _read_json_object_body(
        request=request,
        event_bus=event_bus,
        endpoint_name=f"Video generation {provider_key}",
    )
    raw_model = raw_model_from_native_body(provider_key, body)
    if not raw_model:
        raise HTTPException(status_code=400, detail="Missing required field: model")

    try:
        _ = model_id_from_native(provider_key, raw_model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    workspace_id = workspace_catalog_id_from_native(provider_key, raw_model)
    adapter = forwarder.adapter_type(provider_key)

    try:
        result = await forwarder.forward_video_generation(
            provider=provider_key, request_body=body
        )
        if event_bus:
            await event_bus.publish_nowait(
                CloudProxyRequestForwarded(
                    provider=provider_key,
                    model=workspace_id,
                    streaming=False,
                    adapter_type=adapter,
                    surface="video_generation",
                )
            )
        return JSONResponse(content=result)
    except (httpx.HTTPStatusError, httpx.HTTPError, ValueError) as exc:
        await publish_and_raise(
            event_bus,
            exc=exc,
            provider=provider_key,
            model=workspace_id,
            adapter_type=adapter,
        )


# Backward-compatible aliases for tests and legacy imports.
_get_mcp_config_for_provider = get_mcp_config_for_provider
_inject_native_mcp = inject_native_mcp
_publish_failed = publish_failed
_forward_native = forward_native
_forward_images = forward_images
_forward_video_generation = forward_video_generation
_assert_catalog_model_known = assert_catalog_model_known
