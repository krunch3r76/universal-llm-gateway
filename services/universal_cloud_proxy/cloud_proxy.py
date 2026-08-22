"""
Cloud Proxy Service — credential-isolated forwarding to cloud API providers.

Sole component with outbound internet access. Stargate connects to this
service over loopback to access cloud models without holding API keys.

Endpoints:
    GET  /health              — liveness + provider reachability
    GET  /catalog             — cached model list for Stargate discovery
    GET  /catalog/pricing     — configured models with pricing (routing)
    POST /v1/chat/completions — forward with auth injection + SSE relay
    POST /v1/embeddings       — forward with auth injection
    POST /api/v1/providers/anthropic/messages — native Anthropic Messages API (raw model id)
    POST /api/v1/providers/xai/responses      — native xAI Responses API (raw model id)
    POST /api/v1/providers/openai/responses   — native OpenAI Responses API (raw model id)
    GET  /                    — model browser UI
    GET  /api/models          — full OpenRouter catalog with pricing
    POST /api/refresh         — force re-fetch of routing + browser catalogs
    GET  /api/models/{id}     — single model pricing lookup
"""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from deploy_identity.code_version import resolve_code_version
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from llm_adapters.capability_dispatch import CatalogMissError
from starlette.responses import Response
from universal_event_bus import EventBus, MinimalEventDebugBroadcaster

from .adapters.base import ProviderAdapter
from .adapters.factory import create_provider_adapter
from .browser import BrowserCatalogCache
from .browser_routes import register_browser_routes
from .catalog import CatalogManager
from .config import load_config
from .events import (
    CloudProxyBrowserCatalogRefreshed,
    CloudProxyBrowserCatalogRefreshFailed,
    CloudProxyBrowserUiUnavailable,
    CloudProxyCatalogRefreshed,
    CloudProxyCatalogRefreshFailed,
    CloudProxyDispatchCatalogMiss,
    CloudProxyLocalCatalogRefreshed,
    CloudProxyLocalCatalogUnavailable,
    CloudProxyMcpConfigured,
    CloudProxyRequestFailed,
    CloudProxyRequestForwarded,
    CloudProxyRequestTranslationFailed,
    CloudProxyShutdown,
    CloudProxyStarted,
)
from .forwarder import ProviderForwarder
from .local_catalog import LocalCatalogCache
from .mcp_executor import McpToolExecutor
from .native_boundary import _EFFORT_SUFFIX
from .native_routes import router as native_router

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"
_REQUIRED_BROWSER_ASSETS = ("index.html", "app.js", "style.css")

_atexit_event_bus: EventBus | None = None

# ∀ process exit: _shutdown_clean is set iff lifespan shutdown handler ran.
# atexit fires on clean Python exit (crash, sys.exit) but NOT on SIGKILL.
# Absence of shutdown log + _shutdown_clean=False → crash or unhandled exception.
_shutdown_clean: bool = False


def _atexit_handler() -> None:
    if not _shutdown_clean:
        if _atexit_event_bus is not None:
            try:
                asyncio.run(
                    _atexit_event_bus.publish(CloudProxyShutdown(reason="crash"))
                )
            except RuntimeError as exc:
                logger.debug(
                    "Failed to publish crash shutdown event: %s "
                    "(event loop likely unavailable during interpreter shutdown)",
                    exc,
                    exc_info=True,
                )
            except Exception:
                logger.debug("Failed to publish crash shutdown event", exc_info=True)
        logger.warning(
            "Cloud proxy (PID %d) exited without clean shutdown — "
            "lifespan shutdown handler did not run (crash or unhandled exception). "
            "SIGKILL cannot be detected this way; check for OOM or external kill.",
            os.getpid(),
        )


@asynccontextmanager
async def _lifespan(_application: Any):  # FastAPI lifespan signature.
    """Manage cloud proxy startup and shutdown resource lifecycle."""
    global _shutdown_clean, _atexit_event_bus

    _ = atexit.register(_atexit_handler)

    event_bus = EventBus()
    _atexit_event_bus = event_bus
    broadcaster = MinimalEventDebugBroadcaster(
        uds_publish_path=os.environ.get(
            "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
        ),
    )
    event_bus.set_debug_broadcaster(broadcaster)
    await broadcaster.start_debug_server()

    config = load_config()
    if not config.providers:
        logger.warning("No cloud providers configured — proxy will serve empty catalog")

    shared_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=15.0, read=1800.0, write=15.0, pool=15.0),
        limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
        http2=False,
    )
    adapters: dict[str, ProviderAdapter] = {
        cfg.provider: create_provider_adapter(cfg, shared_client, event_bus=event_bus)
        for cfg in config.providers
    }
    forwarder = ProviderForwarder(adapters=adapters)

    async def _emit_catalog_refreshed(provider: str, model_count: int) -> None:
        await event_bus.publish(
            CloudProxyCatalogRefreshed(provider=provider, model_count=model_count)
        )

    async def _emit_catalog_refresh_failed(provider: str, error: str) -> None:
        await event_bus.publish(
            CloudProxyCatalogRefreshFailed(provider=provider, error=error[:300])
        )

    async def _emit_dispatch_catalog_miss(
        provider: str, model_id: str, reason: str
    ) -> None:
        await event_bus.publish(
            CloudProxyDispatchCatalogMiss(
                provider=provider, model_id=model_id, reason=reason[:300]
            )
        )

    catalog = CatalogManager(
        config.providers,
        adapters,
        on_provider_catalog_refreshed=_emit_catalog_refreshed,
        on_provider_catalog_refresh_failed=_emit_catalog_refresh_failed,
        on_dispatch_catalog_miss=_emit_dispatch_catalog_miss,
    )
    await catalog.startup()

    browser_cache = BrowserCatalogCache()
    try:
        model_count = await browser_cache.refresh()
        await event_bus.publish(
            CloudProxyBrowserCatalogRefreshed(
                trigger="startup",
                model_count=model_count,
            )
        )
    except Exception as exc:
        logger.warning(
            "Browser catalog initial fetch failed — will retry on first request"
        )
        await event_bus.publish(
            CloudProxyBrowserCatalogRefreshFailed(
                trigger="startup",
                error=str(exc)[:300],
            )
        )

    local_cache = LocalCatalogCache(stargate_url=config.stargate_url)
    try:
        local_count = await local_cache.refresh()
        await event_bus.publish(
            CloudProxyLocalCatalogRefreshed(
                stargate_url=config.stargate_url,
                model_count=local_count,
            )
        )
    except Exception as exc:
        logger.info(
            "Local catalog unavailable at startup (Stargate may not be running): %s",
            exc,
        )
        await event_bus.publish(
            CloudProxyLocalCatalogUnavailable(
                stargate_url=config.stargate_url,
                error=str(exc)[:300],
            )
        )

    mcp_executor: McpToolExecutor | None = None
    if config.mcp_server_url:
        mcp_executor = McpToolExecutor(
            mcp_url=config.mcp_server_url,
            auth_token=config.mcp_auth_token,
        )
        await mcp_executor.startup()

    missing_assets = [
        asset
        for asset in _REQUIRED_BROWSER_ASSETS
        if not (_STATIC_DIR / asset).exists()
    ]
    if missing_assets:
        browser_ui_ready = False
        browser_ui_error = f"Missing browser assets: {', '.join(missing_assets)}"
        logger.error(browser_ui_error)
        await event_bus.publish(
            CloudProxyBrowserUiUnavailable(missing_files=missing_assets)
        )
    else:
        browser_ui_ready = True
        browser_ui_error = ""

    await event_bus.publish(
        CloudProxyStarted(
            pid=os.getpid(),
            mode="uds" if config.socket_path else "tcp",
            socket_path=str(config.socket_path) if config.socket_path else None,
        )
    )
    for provider_cfg in config.providers:
        if provider_cfg.mcp_server_url:
            await event_bus.publish(
                CloudProxyMcpConfigured(
                    provider=provider_cfg.provider,
                    mcp_server_url=provider_cfg.mcp_server_url,
                )
            )
    logger.info(
        "Cloud proxy started: %d provider(s), %d models, browser catalog: %d, mode: %s",
        len(config.providers),
        len(catalog.get_all_models()),
        browser_cache.model_count,
        "uds" if config.socket_path else "tcp",
    )

    _application.state.config = config
    _application.state.catalog = catalog
    _application.state.forwarder = forwarder
    _application.state.shared_client = shared_client
    _application.state.event_bus = event_bus
    _application.state.broadcaster = broadcaster
    _application.state.browser_cache = browser_cache
    _application.state.local_cache = local_cache
    _application.state.mcp_executor = mcp_executor
    _application.state.browser_ui_ready = browser_ui_ready
    _application.state.browser_ui_error = browser_ui_error

    yield

    _shutdown_clean = True
    await event_bus.publish(CloudProxyShutdown(reason="clean"))
    await catalog.shutdown()
    if mcp_executor:
        await mcp_executor.shutdown()
    await shared_client.aclose()
    await broadcaster.stop_debug_server()
    logger.info("Cloud proxy shut down")


app = FastAPI(title="Cloud Proxy", lifespan=_lifespan)


async def _publish_request_failed_event(
    *,
    event_bus: EventBus | None,
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


async def _publish_translation_failed_event(
    *,
    event_bus: EventBus | None,
    provider: str,
    model: str,
    error: str,
    direction: str,
    adapter_type: str,
) -> None:
    if event_bus is None:
        return
    await event_bus.publish(
        CloudProxyRequestTranslationFailed(
            provider=provider,
            model=model,
            error=error,
            direction=direction,
            adapter_type=adapter_type,
        )
    )


def catalog_miss_http_detail(exc: CatalogMissError) -> dict[str, str]:
    """Typed 4xx body for an uncarded dispatch — never Internal Server Error."""
    return {
        "error": "catalog_miss",
        "miss_key": exc.miss_key,
        "miss_reason": exc.miss_reason,
        "message": str(exc),
    }


async def raise_catalog_miss_http(
    *,
    exc: CatalogMissError,
    event_bus: EventBus | None,
    provider: str,
    model: str,
    adapter_type: str,
) -> None:
    """Publish catalog-miss + request-failed, then raise HTTP 422."""
    detail = catalog_miss_http_detail(exc)
    if event_bus is not None:
        await event_bus.publish(
            CloudProxyDispatchCatalogMiss(
                provider=provider,
                model_id=model,
                reason=exc.miss_reason[:300],
            )
        )
    await _publish_request_failed_event(
        event_bus=event_bus,
        provider=provider,
        model=model,
        status_code=422,
        error=str(exc)[:300],
        adapter_type=adapter_type,
    )
    raise HTTPException(status_code=422, detail=detail) from exc


async def _read_json_object_body(
    *, request: Request, event_bus: EventBus | None, endpoint_name: str
) -> dict[str, Any]:
    try:
        request_body = await request.json()
    except json.JSONDecodeError as exc:
        error_msg = f"Invalid JSON body: {exc.msg}"
        logger.error("%s request failed: %s", endpoint_name, error_msg)
        await _publish_request_failed_event(
            event_bus=event_bus,
            provider="unknown",
            model="",
            status_code=400,
            error=error_msg,
            adapter_type="unknown",
        )
        raise HTTPException(status_code=400, detail=error_msg) from exc
    if isinstance(request_body, dict):
        return request_body

    error_msg = "Invalid request body: expected a JSON object"
    logger.error("%s request failed: %s", endpoint_name, error_msg)
    await _publish_request_failed_event(
        event_bus=event_bus,
        provider="unknown",
        model="",
        status_code=400,
        error=error_msg,
        adapter_type="unknown",
    )
    raise HTTPException(status_code=400, detail=error_msg)


async def _json_completion_to_sse(
    completion: dict[str, Any],
) -> AsyncIterator[bytes]:
    """Wrap a non-streaming chat completion JSON as SSE chunks.

    Used when the proxy-side tool loop produces a final JSON response but the
    original client request had ``stream: true``.
    """
    choices = completion.get("choices", [])
    msg = choices[0].get("message", {}) if choices else {}
    chunk_id = completion.get("id", f"chatcmpl-{int(time.time() * 1000)}")
    created = completion.get("created", int(time.time()))
    model = completion.get("model", "")

    delta_role: dict[str, Any] = {"role": msg.get("role", "assistant")}
    chunk = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta_role, "finish_reason": None}],
    }
    yield f"data: {json.dumps(chunk)}\n\n".encode()

    content = msg.get("content")
    if content:
        chunk["choices"] = [
            {"index": 0, "delta": {"content": content}, "finish_reason": None}
        ]
        yield f"data: {json.dumps(chunk)}\n\n".encode()

    finish_reason = choices[0].get("finish_reason", "stop") if choices else "stop"
    chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": finish_reason}]
    if "usage" in completion:
        chunk["usage"] = completion["usage"]
    yield f"data: {json.dumps(chunk)}\n\n".encode()
    yield b"data: [DONE]\n\n"


async def _relay_stream_safe(
    chunks: AsyncIterator[bytes],
    provider: str,
    model_id: str,
    adapter_type: str,
    event_bus: EventBus | None = None,
) -> AsyncIterator[bytes]:
    """Relay SSE chunks, converting upstream errors to SSE error events.

    ∀ error raised after HTTP 200 headers are committed: yield SSE error + [DONE]
    rather than aborting the chunked transfer (which produces an incomplete-read
    error on the caller side).
    """
    try:
        async for chunk in chunks:
            yield chunk
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        full_error_text = str(exc)
        error_text = full_error_text[:300]
        logger.error(
            "Streaming %d from provider %s model=%s: %s",
            status,
            provider,
            model_id,
            full_error_text,
        )
        await _publish_request_failed_event(
            event_bus=event_bus,
            provider=provider,
            model=model_id,
            status_code=status,
            error=error_text,
            adapter_type=adapter_type,
        )
        # Avoid duplicating prefix from forwarder errors that already start with
        # "Provider returned <status>: ..."
        message = (
            error_text
            if error_text.startswith(f"Provider returned {status}: ")
            else f"Provider returned {status}: {error_text[:200]}"
        )
        error_dict = {
            "error": {
                "message": message,
                "type": "provider_error",
                "code": str(status),
            }
        }
        yield f"data: {json.dumps(error_dict)}\n\n".encode()
        yield b"data: [DONE]\n\n"
    except CatalogMissError as exc:
        error_text = str(exc)[:300]
        logger.error(
            "Streaming catalog-miss from provider %s model=%s: %s",
            provider,
            model_id,
            error_text,
        )
        if event_bus is not None:
            await event_bus.publish(
                CloudProxyDispatchCatalogMiss(
                    provider=provider,
                    model_id=model_id,
                    reason=exc.miss_reason[:300],
                )
            )
        await _publish_request_failed_event(
            event_bus=event_bus,
            provider=provider,
            model=model_id,
            status_code=422,
            error=error_text,
            adapter_type=adapter_type,
        )
        error_dict = {
            "error": {
                "message": error_text,
                "type": "catalog_miss",
                "code": "422",
                "miss_key": exc.miss_key,
                "miss_reason": exc.miss_reason,
            }
        }
        yield f"data: {json.dumps(error_dict)}\n\n".encode()
        yield b"data: [DONE]\n\n"
    except ValueError as exc:
        error_text = str(exc)[:300]
        logger.error(
            "Streaming translation error from provider %s model=%s: %s",
            provider,
            model_id,
            error_text,
        )
        await _publish_translation_failed_event(
            event_bus=event_bus,
            provider=provider,
            model=model_id,
            error=error_text,
            direction="stream_chunk",
            adapter_type=adapter_type,
        )
        await _publish_request_failed_event(
            event_bus=event_bus,
            provider=provider,
            model=model_id,
            status_code=502,
            error=error_text,
            adapter_type=adapter_type,
        )
        error_dict = {
            "error": {
                "message": error_text,
                "type": "translation_error",
                "code": "stream_translation_error",
            }
        }
        yield f"data: {json.dumps(error_dict)}\n\n".encode()
        yield b"data: [DONE]\n\n"
    except Exception as exc:
        full_error_text = str(exc)
        error_text = full_error_text[:300]
        logger.error(
            "Streaming error from provider %s model=%s: %s",
            provider,
            model_id,
            full_error_text,
        )
        await _publish_request_failed_event(
            event_bus=event_bus,
            provider=provider,
            model=model_id,
            status_code=502,
            error=error_text,
            adapter_type=adapter_type,
        )
        error_dict = {
            "error": {
                "message": error_text,
                "type": "upstream_error",
                "code": "stream_error",
            }
        }
        yield f"data: {json.dumps(error_dict)}\n\n".encode()
        yield b"data: [DONE]\n\n"


def _get_browser_cache() -> BrowserCatalogCache | None:
    return getattr(app.state, "browser_cache", None)


def _get_local_cache() -> LocalCatalogCache | None:
    return getattr(app.state, "local_cache", None)


def _get_catalog() -> CatalogManager | None:
    return getattr(app.state, "catalog", None)


def _get_forwarder() -> ProviderForwarder | None:
    return getattr(app.state, "forwarder", None)


def _get_event_bus() -> EventBus | None:
    return getattr(app.state, "event_bus", None)


def _get_mcp_executor() -> McpToolExecutor | None:
    return getattr(app.state, "mcp_executor", None)


def _get_ui_status() -> tuple[bool, str]:
    return (
        bool(getattr(app.state, "browser_ui_ready", False)),
        str(getattr(app.state, "browser_ui_error", "Browser UI not initialized")),
    )


register_browser_routes(
    app,
    static_dir=_STATIC_DIR,
    get_browser_cache=_get_browser_cache,
    get_local_cache=_get_local_cache,
    get_catalog=_get_catalog,
    get_event_bus=_get_event_bus,
    get_ui_status=_get_ui_status,
)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness check with provider summary."""
    catalog = _get_catalog()
    assert catalog is not None
    models = catalog.get_all_models()
    providers = {m["provider"] for m in models}
    return {
        "status": "ok",
        "providers": sorted(providers),
        "model_count": len(models),
        "code_version": resolve_code_version(),
    }


@app.get("/catalog")
async def catalog() -> list[dict[str, Any]]:
    """Return the cached model catalog for Stargate consumption."""
    catalog_manager = _get_catalog()
    assert catalog_manager is not None
    return catalog_manager.get_all_models()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    """Forward chat completion requests to the resolved cloud provider.

    Handles both streaming and non-streaming OpenAI-compatible chat requests.
    The provider is resolved from the incoming `model` field, and the request
    is relayed through the provider adapter with auth injection handled by the
    cloud proxy internals.

    Contract: this route always preserves the OpenAI-compatible chat surface.
    Providers with non-chat-native upstream APIs (for example xAI MCP requests
    routed through ``/v1/responses``) are translated into chat-completions JSON
    or ``chat.completion.chunk`` SSE frames rather than being passed through
    verbatim.
    """
    catalog = _get_catalog()
    forwarder = _get_forwarder()
    event_bus = _get_event_bus()
    assert catalog is not None
    assert forwarder is not None

    body = await _read_json_object_body(
        request=request,
        event_bus=event_bus,
        endpoint_name="Chat completions",
    )
    model_id = str(body.get("model", ""))
    streaming = body.get("stream", False)

    mcp_requested = model_id.endswith("-mcp")
    if mcp_requested:
        model_id = model_id[:-4]
        body["model"] = model_id

    # Decode __effort_<level> suffix so resolve_provider sees a bare base ID.
    # Caller-supplied reasoning.effort in the body takes precedence (caller wins).
    _injected_effort: str | None = None
    if _EFFORT_SUFFIX in model_id:
        model_id, _injected_effort = model_id.split(_EFFORT_SUFFIX, 1)
        body["model"] = model_id

    provider_catalog = catalog.resolve_provider(model_id)
    if provider_catalog is None:
        await _publish_request_failed_event(
            event_bus=event_bus,
            provider="unknown",
            model=model_id,
            status_code=404,
            error=f"Model not found: {model_id}",
            adapter_type="unknown",
        )
        raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")

    try:
        adapter = forwarder.adapter_type(provider_catalog.provider)
    except ValueError as exc:
        error_text = str(exc)[:300]
        await _publish_translation_failed_event(
            event_bus=event_bus,
            provider=provider_catalog.provider,
            model=model_id,
            error=error_text,
            direction="request",
            adapter_type="unknown",
        )
        await _publish_request_failed_event(
            event_bus=event_bus,
            provider=provider_catalog.provider,
            model=model_id,
            status_code=500,
            error=error_text,
            adapter_type="unknown",
        )
        raise HTTPException(status_code=500, detail=error_text) from exc

    # xAI multi-agent models only work on the Responses API — route them
    # through the bridge regardless of -mcp suffix.
    _xai_multi_agent = (
        provider_catalog.provider == "xai" and "multi-agent" in model_id.lower()
    )
    # xAI grok-4+: Responses API bridge (provider connects to MCP server,
    # server-side tool loop).  Only grok-4 family supports type:"mcp" tools.
    _xai_responses_api = False
    if provider_catalog.provider == "xai" and (
        _xai_multi_agent or (mcp_requested and "grok-4" in model_id.lower())
    ):
        body["_mcp_requested"] = True
        _xai_responses_api = True

    # Inject reasoning.effort decoded from model suffix (xAI only).
    # Caller-supplied reasoning.effort in the body takes precedence.
    if _injected_effort is not None and "effort" not in body.get("reasoning", {}):
        body.setdefault("reasoning", {})["effort"] = _injected_effort

    # Resolve cortex_brief directives in system prompt before any MCP path.
    # The proxy-side loop does this inside run_tool_loop; the Responses API
    # bridge bypasses that, so we resolve here for the bridge path.
    if _xai_responses_api:
        _boot_exec = _get_mcp_executor()
        if _boot_exec and _boot_exec.available:
            await _boot_exec._resolve_boot_directive(body.get("messages", []))

    # Proxy-side tool loop: inject MCP tool defs as standard function tools,
    # execute tool_calls locally via MCP server, re-submit.  Works with any
    # model that supports function calling.  Avoids requiring the provider to
    # connect back to our MCP server (firewall, connectivity issues).
    _proxy_loop_providers = {"anthropic", "google", "openrouter", "openai"}
    _use_proxy_loop = mcp_requested and (
        provider_catalog.provider in _proxy_loop_providers
        or (provider_catalog.provider == "xai" and not _xai_responses_api)
    )
    mcp_executor = _get_mcp_executor()
    if _use_proxy_loop:
        if not mcp_executor or not mcp_executor.available:
            raise HTTPException(
                status_code=503,
                detail="MCP tool executor not available — proxy tool loop unavailable",
            )

        async def _proxy_forward(b: dict[str, Any]) -> dict[str, Any]:
            return await forwarder.forward_chat_request(
                provider=provider_catalog.provider, request_body=b
            )

        try:
            response_json = await mcp_executor.run_tool_loop(_proxy_forward, body)
        except CatalogMissError as exc:
            await raise_catalog_miss_http(
                exc=exc,
                event_bus=event_bus,
                provider=provider_catalog.provider,
                model=model_id,
                adapter_type=adapter,
            )
        if event_bus:
            await event_bus.publish(
                CloudProxyRequestForwarded(
                    provider=provider_catalog.provider,
                    model=model_id,
                    streaming=False,
                    adapter_type=adapter,
                    mcp_injected=True,
                )
            )
        if streaming:
            return StreamingResponse(
                _json_completion_to_sse(response_json),
                media_type="text/event-stream",
            )
        return JSONResponse(content=response_json)

    if streaming:
        chunks = forwarder.forward_request_stream(
            provider=provider_catalog.provider,
            request_body=body,
        )
        if event_bus:
            await event_bus.publish(
                CloudProxyRequestForwarded(
                    provider=provider_catalog.provider,
                    model=model_id,
                    streaming=True,
                    adapter_type=adapter,
                    mcp_injected=mcp_requested,
                )
            )
        return StreamingResponse(
            _relay_stream_safe(
                chunks,
                provider_catalog.provider,
                model_id,
                adapter,
                event_bus=event_bus,
            ),
            media_type="text/event-stream",
        )

    try:
        response_json = await forwarder.forward_chat_request(
            provider=provider_catalog.provider,
            request_body=body,
        )
        if event_bus:
            await event_bus.publish(
                CloudProxyRequestForwarded(
                    provider=provider_catalog.provider,
                    model=model_id,
                    streaming=False,
                    adapter_type=adapter,
                    mcp_injected=mcp_requested,
                )
            )
        return JSONResponse(content=response_json)

    except CatalogMissError as exc:
        await raise_catalog_miss_http(
            exc=exc,
            event_bus=event_bus,
            provider=provider_catalog.provider,
            model=model_id,
            adapter_type=adapter,
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        error_text = str(exc)[:300]
        await _publish_request_failed_event(
            event_bus=event_bus,
            provider=provider_catalog.provider,
            model=model_id,
            status_code=status,
            error=error_text,
            adapter_type=adapter,
        )
        raise HTTPException(
            status_code=status, detail=f"Upstream provider error: {error_text}"
        ) from exc
    except httpx.HTTPError as exc:
        error_text = str(exc)[:300]
        await _publish_request_failed_event(
            event_bus=event_bus,
            provider=provider_catalog.provider,
            model=model_id,
            status_code=502,
            error=error_text,
            adapter_type=adapter,
        )
        raise HTTPException(status_code=502, detail=error_text) from exc
    except ValueError as exc:
        error_text = str(exc)[:300]
        await _publish_translation_failed_event(
            event_bus=event_bus,
            provider=provider_catalog.provider,
            model=model_id,
            error=error_text,
            direction="request",
            adapter_type=adapter,
        )
        await _publish_request_failed_event(
            event_bus=event_bus,
            provider=provider_catalog.provider,
            model=model_id,
            status_code=422,
            error=error_text,
            adapter_type=adapter,
        )
        raise HTTPException(status_code=422, detail=error_text) from exc


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> JSONResponse:
    """Forward embedding requests to the resolved cloud provider.

    The provider is resolved from the incoming `model` field in the request
    body. The request is then relayed through the configured provider adapter.
    """
    catalog = _get_catalog()
    forwarder = _get_forwarder()
    event_bus = _get_event_bus()
    assert catalog is not None
    assert forwarder is not None

    body = await _read_json_object_body(
        request=request,
        event_bus=event_bus,
        endpoint_name="Embeddings",
    )
    model_id = str(body.get("model", ""))

    provider_catalog = catalog.resolve_provider(model_id)
    if provider_catalog is None:
        await _publish_request_failed_event(
            event_bus=event_bus,
            provider="unknown",
            model=model_id,
            status_code=404,
            error=f"Model not found: {model_id}",
            adapter_type="unknown",
        )
        raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")
    try:
        adapter = forwarder.adapter_type(provider_catalog.provider)
    except ValueError as exc:
        error_text = str(exc)[:300]
        await _publish_translation_failed_event(
            event_bus=event_bus,
            provider=provider_catalog.provider,
            model=model_id,
            error=error_text,
            direction="request",
            adapter_type="unknown",
        )
        await _publish_request_failed_event(
            event_bus=event_bus,
            provider=provider_catalog.provider,
            model=model_id,
            status_code=500,
            error=error_text,
            adapter_type="unknown",
        )
        raise HTTPException(status_code=500, detail=error_text) from exc

    try:
        result = await forwarder.forward_embedding_request(
            provider=provider_catalog.provider,
            request_body=body,
        )
        if event_bus:
            await event_bus.publish(
                CloudProxyRequestForwarded(
                    provider=provider_catalog.provider,
                    model=model_id,
                    streaming=False,
                    adapter_type=adapter,
                )
            )
        return JSONResponse(content=result)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        error_text = str(exc)[:300]
        await _publish_request_failed_event(
            event_bus=event_bus,
            provider=provider_catalog.provider,
            model=model_id,
            status_code=status,
            error=error_text,
            adapter_type=adapter,
        )
        raise HTTPException(
            status_code=status, detail=f"Upstream provider error: {error_text}"
        ) from exc
    except httpx.HTTPError as exc:
        error_text = str(exc)[:300]
        await _publish_request_failed_event(
            event_bus=event_bus,
            provider=provider_catalog.provider,
            model=model_id,
            status_code=502,
            error=error_text,
            adapter_type=adapter,
        )
        raise HTTPException(status_code=502, detail=error_text) from exc
    except ValueError as exc:
        error_text = str(exc)[:300]
        await _publish_translation_failed_event(
            event_bus=event_bus,
            provider=provider_catalog.provider,
            model=model_id,
            error=error_text,
            direction="request",
            adapter_type=adapter,
        )
        await _publish_request_failed_event(
            event_bus=event_bus,
            provider=provider_catalog.provider,
            model=model_id,
            status_code=422,
            error=error_text,
            adapter_type=adapter,
        )
        raise HTTPException(status_code=422, detail=error_text) from exc


app.include_router(native_router)
