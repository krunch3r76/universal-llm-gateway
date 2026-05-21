"""Provider-native ingress routes (cloud proxy).

Single shared dispatcher for all provider-native endpoints.  Each route is a
thin wrapper that passes ``provider_key`` and a ``surface`` telemetry tag to
``_forward_native``.  Adapter dispatch, auth injection, and event publishing
are handled once — adding a new provider route requires only a new 3-line
wrapper plus a forwarder entry.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from llm_adapters._mcp_entry import (
    anthropic_mcp_server_entry,
    openai_xai_mcp_tool_entry,
)
from universal_event_bus import EventBus
from universal_logging import get_logger

from .events import CloudProxyRequestFailed, CloudProxyRequestForwarded
from .forwarder import ProviderForwarder
from .native_boundary import (
    model_id_from_native,
    raw_model_from_native_body,
    workspace_catalog_id_from_native,
)
from .native_streaming import preflight_native_byte_stream

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/providers", tags=["provider-native"])

_MCP_SERVER_NAME = "vortex"
_EFFORT_SUFFIX = "__effort_"


def _inject_native_mcp(provider_key: str, body: dict) -> None:
    """Inject provider-appropriate MCP entry into a native request body.

    Delegates to the shared helpers in ``libs/llm_adapters/_mcp_entry`` so
    the external-wire (``-mcp`` suffix) path and the adapter/pipeline path
    produce byte-identical descriptor shapes.

    - Anthropic: ``mcp_servers`` list in body
    - OpenAI/xAI: ``type: "mcp"`` tool in ``tools`` array
    """
    from .cloud_proxy import _get_mcp_executor

    executor = _get_mcp_executor()
    if executor is None:
        return

    config = _get_mcp_config_for_provider(provider_key)
    if not config or not config.get("token"):
        return

    url = config["url"]
    token = config["token"]

    if provider_key == "anthropic":
        existing = body.get("mcp_servers") or []
        body["mcp_servers"] = [
            *existing,
            anthropic_mcp_server_entry(url, token, name=_MCP_SERVER_NAME),
        ]
    elif provider_key in {"openai", "xai"}:
        if provider_key == "xai":
            logger.info(
                "remote MCP injection skipped — xAI does not yet support type:mcp"
            )
            return
        existing_tools = body.get("tools") or []
        body["tools"] = [
            *existing_tools,
            openai_xai_mcp_tool_entry(url, token, name=_MCP_SERVER_NAME),
        ]


def _get_mcp_config_for_provider(provider_key: str) -> dict | None:
    """Resolve MCP server URL and auth token for a provider."""
    from .cloud_proxy import app

    config = getattr(app.state, "config", None)
    if config is None:
        return None

    for p in config.providers:
        if p.provider == provider_key and p.mcp_server_url:
            return {"url": p.mcp_server_url, "token": p.mcp_auth_token}

    if config.mcp_server_url:
        return {"url": config.mcp_server_url, "token": config.mcp_auth_token}
    return None


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
    await event_bus.publish(
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

    # Strip -mcp suffix — upstream providers don't accept this Stargate annotation.
    mcp_requested = raw_model.endswith("-mcp")
    if mcp_requested:
        raw_model = raw_model[:-4]
        body = {**body, "model": raw_model}
        _inject_native_mcp(provider_key, body)

    # Decode __effort_<value> model suffix for xAI Responses API.
    # The grok CLI silently ignores --reasoning-effort for custom stanzas;
    # effort tier is encoded out-of-band in the model name so cloud-proxy
    # can inject reasoning.effort before the request reaches xAI.
    # Suffix is stripped here so upstream validation sees a clean model ID.
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

    # Inject reasoning.effort decoded from model suffix.
    # Caller-supplied reasoning.effort in the body takes precedence (caller wins).
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
        chunks = forwarder.forward_native_stream(
            provider=provider_key, request_body=body
        )
        try:
            primed = await preflight_native_byte_stream(chunks)
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

        if event_bus:
            await event_bus.publish(
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
            await event_bus.publish(
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


async def _forward_images(
    request: Request,
    *,
    provider_key: str,
    image_endpoint: str,
    surface: str,
    forwarder: ProviderForwarder,
    event_bus: EventBus | None,
) -> Response:
    """Shared handler for image generation/editing routes.

    ``image_endpoint`` is ``"generations"`` or ``"edits"``.  Request/response
    bodies stay in provider-native form (OpenAI images-API shape).  No streaming.
    """
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
            await event_bus.publish(
                CloudProxyRequestForwarded(
                    provider=provider_key,
                    model=workspace_id,
                    streaming=False,
                    adapter_type=forwarder.adapter_type(provider_key),
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
            adapter_type=forwarder.adapter_type(provider_key),
        )
        raise HTTPException(
            status_code=status,
            detail=f"Upstream provider error: {error_text}",
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        error_text = str(exc)[:300]
        await _publish_failed(
            event_bus,
            provider=provider_key,
            model=workspace_id,
            status_code=502,
            error=error_text,
            adapter_type=forwarder.adapter_type(provider_key),
        )
        raise HTTPException(status_code=502, detail=error_text) from exc


async def _forward_video_generation(
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
            await event_bus.publish(
                CloudProxyRequestForwarded(
                    provider=provider_key,
                    model=workspace_id,
                    streaming=False,
                    adapter_type=adapter,
                    surface="video_generation",
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


@router.post("/xai/images/generations")
async def xai_images_generations(request: Request) -> Response:
    """xAI image generation — POST /v1/images/generations proxy."""
    return await _forward_images(
        request,
        provider_key="xai",
        image_endpoint="generations",
        surface="xai_images_generations",
        forwarder=_get_forwarder(),
        event_bus=_get_event_bus(),
    )


@router.post("/xai/images/edits")
async def xai_images_edits(request: Request) -> Response:
    """xAI image editing — POST /v1/images/edits proxy (JSON body, not multipart)."""
    return await _forward_images(
        request,
        provider_key="xai",
        image_endpoint="edits",
        surface="xai_images_edits",
        forwarder=_get_forwarder(),
        event_bus=_get_event_bus(),
    )


@router.post("/openai/images/generations")
async def openai_images_generations(request: Request) -> Response:
    """OpenAI image generation — POST /v1/images/generations proxy."""
    return await _forward_images(
        request,
        provider_key="openai",
        image_endpoint="generations",
        surface="openai_images_generations",
        forwarder=_get_forwarder(),
        event_bus=_get_event_bus(),
    )


@router.post("/openai/images/edits")
async def openai_images_edits(request: Request) -> Response:
    """OpenAI image editing — POST /v1/images/edits proxy."""
    return await _forward_images(
        request,
        provider_key="openai",
        image_endpoint="edits",
        surface="openai_images_edits",
        forwarder=_get_forwarder(),
        event_bus=_get_event_bus(),
    )


@router.post("/xai/videos/generations")
async def xai_videos_generations(request: Request) -> Response:
    """xAI video generation — submit async job, returns request_id."""
    return await _forward_video_generation(
        request,
        provider_key="xai",
        forwarder=_get_forwarder(),
        event_bus=_get_event_bus(),
    )


@router.post("/google/videos/generations")
async def google_videos_generations(request: Request) -> Response:
    """Google Veo video generation — submit async operation, returns request_id."""
    return await _forward_video_generation(
        request,
        provider_key="google",
        forwarder=_get_forwarder(),
        event_bus=_get_event_bus(),
    )


@router.get("/xai/videos/{request_id}")
# xAI request_ids are UUIDs (no slashes) — single-segment param is correct.
# If xAI ever changes format to include '/', both this route and the Stargate
# passthrough will silently 404; switch to {request_id:path} at that point.
# Lifecycle events (_publish_failed / CloudProxyRequestForwarded) are
# intentionally omitted here: status polls are high-frequency and the
# meaningful lifecycle events are emitted on submission (_forward_video_generation).
async def xai_video_status(request_id: str) -> Response:
    """xAI video status — poll for completion by request_id."""
    fwd = _get_forwarder()
    try:
        result = await fwd.forward_video_status(provider="xai", request_id=request_id)
        return JSONResponse(content=result)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        raise HTTPException(
            status_code=status,
            detail=f"Upstream provider error: {str(exc)[:300]}",
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:300]) from exc


@router.get("/google/videos/{request_id:path}")
# Google operation names are slash-separated paths (e.g. "operations/abc123/..."),
# so :path is required. Lifecycle events omitted for the same reason as xai_video_status.
async def google_video_status(request_id: str) -> Response:
    """Google Veo video status — poll for operation completion by request_id."""
    fwd = _get_forwarder()
    try:
        result = await fwd.forward_video_status(
            provider="google", request_id=request_id
        )
        return JSONResponse(content=result)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        raise HTTPException(
            status_code=status,
            detail=f"Upstream provider error: {str(exc)[:300]}",
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:300]) from exc


def _get_forwarder() -> ProviderForwarder:
    from .cloud_proxy import _get_forwarder as _gf

    fwd = _gf()
    assert fwd is not None
    return fwd


def _get_event_bus() -> EventBus | None:
    from .cloud_proxy import _get_event_bus as _ge

    return _ge()


@router.post("/google/generateContent")
async def native_google_generate_content(request: Request) -> Response:
    """Google Gemini generateContent — native body shape, model in body for routing."""
    return await _forward_native(
        request,
        provider_key="google",
        surface="google_generate_content",
        forwarder=_get_forwarder(),
        event_bus=_get_event_bus(),
    )


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
    """xAI Responses API — native body shape, raw model id, raw streaming surface.

    Unlike ``/v1/chat/completions``, this route is the provider-native ingress:
    request/response bodies and SSE framing stay in Responses-API form.
    """
    return await _forward_native(
        request,
        provider_key="xai",
        surface="xai_responses",
        forwarder=_get_forwarder(),
        event_bus=_get_event_bus(),
    )


@router.post("/openai/responses")
async def native_openai_responses(request: Request) -> Response:
    """OpenAI Responses API — native body shape, raw model id, raw streaming surface."""
    return await _forward_native(
        request,
        provider_key="openai",
        surface="openai_responses",
        forwarder=_get_forwarder(),
        event_bus=_get_event_bus(),
    )
