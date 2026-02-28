"""
Cloud Proxy Service — credential-isolated forwarding to cloud API providers.

Sole component with outbound internet access. Stargate connects to this
service over loopback to access cloud models without holding API keys.

Endpoints:
    GET  /health              — liveness + provider reachability
    GET  /catalog             — cached model list for Stargate discovery
    POST /v1/chat/completions — forward with auth injection + SSE relay
    POST /v1/embeddings       — forward with auth injection
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from universal_event_bus import EventBus, MinimalEventDebugBroadcaster
from starlette.responses import Response

from .catalog import CatalogManager
from .config import CloudProxyConfig, load_config
from .events import (
    CloudProxyCatalogRefreshed,
    CloudProxyRequestFailed,
    CloudProxyRequestForwarded,
    CloudProxyShutdown,
    CloudProxyStarted,
)
from .forwarder import ProviderForwarder

logger = logging.getLogger(__name__)

_config: CloudProxyConfig | None = None
_catalog: CatalogManager | None = None
_forwarder: ProviderForwarder | None = None
_event_bus: EventBus | None = None
_broadcaster: MinimalEventDebugBroadcaster | None = None


@asynccontextmanager
async def _lifespan(application: FastAPI):  # noqa: ANN201, ARG001
    global _config, _catalog, _forwarder, _event_bus, _broadcaster

    _event_bus = EventBus()
    _broadcaster = MinimalEventDebugBroadcaster(
        persistence_config={
            "enabled": True,
            "directory": "/tmp/cloud-proxy-events",
            "max_file_size_mb": 10,
            "max_files": 2,
            "flush_interval_seconds": 1.0,
        },
    )
    _event_bus.set_debug_broadcaster(_broadcaster)
    await _broadcaster.start_debug_server()

    _config = load_config()
    if not _config.providers:
        logger.warning("No cloud providers configured — proxy will serve empty catalog")

    _forwarder = ProviderForwarder()
    _catalog = CatalogManager(_config.providers)
    await _catalog.startup()

    for provider_cfg in _config.providers:
        catalog_models = _catalog.get_all_models()
        provider_count = sum(
            1 for m in catalog_models if m["provider"] == provider_cfg.provider
        )
        await _event_bus.publish_async(
            CloudProxyCatalogRefreshed(
                provider=provider_cfg.provider, model_count=provider_count
            )
        )

    await _event_bus.publish_async(CloudProxyStarted())
    logger.info(
        "Cloud proxy started: %d provider(s), %d models",
        len(_config.providers),
        len(_catalog.get_all_models()),
    )

    yield

    await _event_bus.publish_async(CloudProxyShutdown())
    await _catalog.shutdown()
    await _forwarder.close()
    if _broadcaster:
        await _broadcaster.stop_debug_server()
    logger.info("Cloud proxy shut down")


app = FastAPI(title="Cloud Proxy", lifespan=_lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness check with provider summary."""
    assert _catalog is not None
    models = _catalog.get_all_models()
    providers = {m["provider"] for m in models}
    return {
        "status": "ok",
        "providers": sorted(providers),
        "model_count": len(models),
    }


@app.get("/catalog")
async def catalog() -> list[dict[str, Any]]:
    """Return the cached model catalog for Stargate consumption."""
    assert _catalog is not None
    return _catalog.get_all_models()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    """Forward chat completion request to the appropriate provider."""
    assert _catalog is not None
    assert _forwarder is not None

    body: dict[str, Any] = await request.json()
    model_id = body.get("model", "")
    streaming = body.get("stream", False)

    provider_catalog = _catalog.resolve_provider(model_id)
    if provider_catalog is None:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")

    try:
        if streaming:
            chunks = _forwarder.forward_request_stream(
                base_url=provider_catalog.base_url,
                api_key=provider_catalog.api_key,
                request_body=body,
            )
            if _event_bus:
                await _event_bus.publish_async(
                    CloudProxyRequestForwarded(
                        provider=provider_catalog.provider,
                        model=model_id,
                        streaming=True,
                    )
                )
            return StreamingResponse(chunks, media_type="text/event-stream")
        else:
            response = await _forwarder.forward_request(
                base_url=provider_catalog.base_url,
                api_key=provider_catalog.api_key,
                request_body=body,
            )
            if _event_bus:
                await _event_bus.publish_async(
                    CloudProxyRequestForwarded(
                        provider=provider_catalog.provider,
                        model=model_id,
                        streaming=False,
                    )
                )
            return JSONResponse(content=response.json())

    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        error_text = str(exc)[:300]
        if _event_bus:
            await _event_bus.publish_async(
                CloudProxyRequestFailed(
                    provider=provider_catalog.provider,
                    model=model_id,
                    status_code=status,
                    error=error_text,
                )
            )
        raise HTTPException(status_code=status, detail=error_text) from exc
    except httpx.HTTPError as exc:
        if _event_bus:
            await _event_bus.publish_async(
                CloudProxyRequestFailed(
                    provider=provider_catalog.provider,
                    model=model_id,
                    status_code=502,
                    error=str(exc)[:300],
                )
            )
        raise HTTPException(status_code=502, detail=str(exc)[:300]) from exc


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> JSONResponse:
    """Forward embedding request to the appropriate provider."""
    assert _catalog is not None
    assert _forwarder is not None

    body: dict[str, Any] = await request.json()
    model_id = body.get("model", "")

    provider_catalog = _catalog.resolve_provider(model_id)
    if provider_catalog is None:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")

    try:
        result = await _forwarder.forward_embedding_request(
            base_url=provider_catalog.base_url,
            api_key=provider_catalog.api_key,
            request_body=body,
        )
        return JSONResponse(content=result)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        raise HTTPException(status_code=status, detail=str(exc)[:300]) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:300]) from exc
