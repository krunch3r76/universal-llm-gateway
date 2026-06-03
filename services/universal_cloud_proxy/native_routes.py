"""Provider-native ingress routes (cloud proxy).

Thin route declarations; forwarder orchestration lives in ``native_forwarders``.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from universal_event_bus import EventBus

from .forwarder import ProviderForwarder
from .native_forwarders import (
    forward_images,
    forward_native,
    forward_video_generation,
)

router = APIRouter(prefix="/api/v1/providers", tags=["provider-native"])


def _get_forwarder() -> ProviderForwarder:
    from .cloud_proxy import _get_forwarder as _gf

    fwd = _gf()
    assert fwd is not None
    return fwd


def _get_event_bus() -> EventBus | None:
    from .cloud_proxy import _get_event_bus as _ge

    return _ge()


@router.post("/xai/images/generations")
async def xai_images_generations(request: Request) -> Response:
    """xAI image generation — POST /v1/images/generations proxy."""
    return await forward_images(
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
    return await forward_images(
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
    return await forward_images(
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
    return await forward_images(
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
    return await forward_video_generation(
        request,
        provider_key="xai",
        forwarder=_get_forwarder(),
        event_bus=_get_event_bus(),
    )


@router.post("/google/videos/generations")
async def google_videos_generations(request: Request) -> Response:
    """Google Veo video generation — submit async operation, returns request_id."""
    return await forward_video_generation(
        request,
        provider_key="google",
        forwarder=_get_forwarder(),
        event_bus=_get_event_bus(),
    )


@router.get("/xai/videos/{request_id}")
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


@router.post("/google/generateContent")
async def native_google_generate_content(request: Request) -> Response:
    """Google Gemini generateContent — native body shape, model in body for routing."""
    return await forward_native(
        request,
        provider_key="google",
        surface="google_generate_content",
        forwarder=_get_forwarder(),
        event_bus=_get_event_bus(),
    )


@router.post("/anthropic/messages")
async def native_anthropic_messages(request: Request) -> Response:
    """Anthropic Messages API — native body shape, raw model id."""
    return await forward_native(
        request,
        provider_key="anthropic",
        surface="anthropic_messages",
        forwarder=_get_forwarder(),
        event_bus=_get_event_bus(),
    )


@router.post("/xai/responses")
async def native_xai_responses(request: Request) -> Response:
    """xAI Responses API — native body shape, raw model id, raw streaming surface."""
    return await forward_native(
        request,
        provider_key="xai",
        surface="xai_responses",
        forwarder=_get_forwarder(),
        event_bus=_get_event_bus(),
    )


@router.post("/openai/responses")
async def native_openai_responses(request: Request) -> Response:
    """OpenAI Responses API — native body shape, raw model id, raw streaming surface."""
    return await forward_native(
        request,
        provider_key="openai",
        surface="openai_responses",
        forwarder=_get_forwarder(),
        event_bus=_get_event_bus(),
    )


# Re-export forwarders for tests that import from native_routes.
from .native_forwarders import (  # noqa: E402
    _forward_native as _forward_native,
    _inject_native_mcp as _inject_native_mcp,
)
