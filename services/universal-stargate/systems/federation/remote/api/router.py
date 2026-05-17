"""
Federation inference router factory (Remote mode).

Orchestrates /api/v1/federation/inference with register-before-await invariant,
topology selection, slot management, and passthrough forwarding delegation.
"""

import asyncio
import json

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from model_id import ModelId, get_compute_type
from universal_logging import get_logger
from universal_protocol import ErrorCode, error_envelope, get_http_status

from ...common.config import FederationConfig
from ...common.types import HEADER_REQUEST_ID
from .edge_relay_forwarding import (
    forward_to_edge_nonstreaming,
    forward_to_edge_streaming,
)
from .federation_error_details import extract_structured_detail
from .gateway_connection import resolve_gateway_connection
from .gateway_forwarding import (
    forward_to_gateway_nonstreaming,
    forward_to_gateway_streaming,
)
from .request_store import ActiveRequestStore
from .slot_management import SlotContext

logger = get_logger(__name__)

# Deferred import to avoid circular dependency at module load
_derive_endpoint_category = None


def _get_endpoint_category(endpoint_path: str) -> str:
    """Get endpoint category (deferred import to avoid cycles)."""
    global _derive_endpoint_category
    if _derive_endpoint_category is None:
        from systems.proxy.core.endpoint_category import derive_endpoint_category

        _derive_endpoint_category = derive_endpoint_category
    return _derive_endpoint_category(path=endpoint_path).value


def create_inference_router(
    config: FederationConfig,
    request_store: ActiveRequestStore,
    gateway_socket_path: str | None = None,
    gateway_url: str | None = None,
    local_edge_client=None,
    gateway_id: str | None = None,
) -> APIRouter:
    """Create federation inference router (POST /api/v1/federation/inference)."""
    router = APIRouter(prefix="/api/v1/federation", tags=["federation"])

    @router.post("/inference")
    async def federation_inference(request: Request):
        """Handle forwarded inference (register before first await; relay or direct)."""
        # Step 1: Extract request_id from header (sync, no await yet)
        request_id = request.headers.get(HEADER_REQUEST_ID)
        if not request_id:
            raise HTTPException(status_code=400, detail="Missing X-Request-ID header")

        # Step 2: Register BEFORE first await - satisfies invariant
        active_req = request_store.register(request_id)

        # Store current task for cancellation (FastAPI request handler task)
        active_req.gateway_task = asyncio.current_task()

        try:
            # Step 3: Parse body (await)
            try:
                body = await request.json()
            except Exception:
                logger.exception(
                    "Invalid request body",
                    extra={"request_id": request_id},
                )
                raise HTTPException(status_code=400, detail="Invalid request body")

            # Relay topology: Forward to Edge via Unix socket
            if local_edge_client:
                federation = body.get("federation", {})
                original_request = body.get("request", {})
                request_id = federation.get("request_id", request_id)

                # Check if client requested streaming
                is_streaming = original_request.get("stream", False)

                logger.debug(
                    "📡 Forwarding inference to Edge (Unix): %s streaming=%s",
                    request_id,
                    is_streaming,
                )

                try:
                    headers = {
                        "X-Request-ID": request_id,
                        "Content-Type": "application/json",
                    }
                    if config and config.stargate_id:
                        headers["X-Federation-Source"] = config.stargate_id
                        headers["X-Federation-Key"] = local_edge_client._config.api_key

                    if is_streaming:
                        return StreamingResponse(
                            forward_to_edge_streaming(
                                local_edge_client._config.socket_path,
                                body,
                                headers,
                            ),
                            media_type="application/newline-delimited-json",
                            headers={
                                "Cache-Control": "no-cache",
                                "Connection": "keep-alive",
                                "X-Accel-Buffering": "no",
                            },
                        )
                    else:
                        result = await forward_to_edge_nonstreaming(
                            local_edge_client._config.socket_path,
                            body,
                            headers,
                        )
                        return JSONResponse(content=result)
                except httpx.HTTPStatusError as e:
                    logger.error(
                        f"❌ Edge inference failed: HTTP {e.response.status_code}"
                    )
                    # Ensure body is read before extracting detail
                    # (streaming responses may not have consumed body yet)
                    try:
                        await e.response.aread()
                    except Exception:
                        pass
                    detail = extract_structured_detail(e.response)
                    raise HTTPException(
                        status_code=e.response.status_code,
                        detail=detail,
                    )
                except Exception as e:
                    logger.exception(f"❌ Failed to forward inference to Edge: {e}")
                    raise HTTPException(
                        status_code=503,
                        detail=error_envelope(
                            code=ErrorCode.EDGE_UNREACHABLE,
                            message=f"Edge connection failed: {e}",
                            source="edge",
                            retryable=True,
                        ),
                    )

            try:
                sock_path, http_url = resolve_gateway_connection(
                    config, gateway_socket_path, gateway_url, purpose="inference"
                )
            except ValueError:
                logger.exception("Gateway connection resolution failed")
                raise HTTPException(
                    status_code=503, detail="Gateway connection unavailable"
                )

            # Extract federation metadata
            federation = body.get("federation", {})
            original_request = body.get("request", {})

            request_id = federation.get("request_id", request_id)
            hop_count = federation.get("hop_count", 0)
            target_gateway = federation.get("target_gateway")
            endpoint = federation.get("endpoint", "/v1/chat/completions")

            # Extract explicit timeout hint (None = no worker-level timeout)
            hints = federation.get("hints", {})
            explicit_timeout: float | None = None
            if "timeout" in hints:
                explicit_timeout = float(hints["timeout"])
                logger.debug(
                    "Using explicit timeout hint: %ss",
                    explicit_timeout,
                    extra={"request_id": request_id},
                )

            logger.info(
                "Received federation inference request",
                extra={
                    "request_id": request_id,
                    "hop_count": hop_count,
                    "source": federation.get("source_stargate"),
                    "target_gateway": target_gateway,
                    "model": original_request.get("model"),
                    "endpoint": endpoint,
                },
            )

            model_str = original_request.get("model", "")

            try:
                endpoint_category = _get_endpoint_category(endpoint)
            except ValueError as e:
                logger.error(f"❌ Unknown endpoint {endpoint}: {e}")
                raise HTTPException(
                    status_code=400,
                    detail=error_envelope(
                        code=ErrorCode.INVALID_ENDPOINT,
                        message=f"Unknown endpoint: {endpoint}",
                        source="edge",
                        retryable=False,
                        data={"endpoint": endpoint},
                    ),
                )

            try:
                model_id = ModelId.parse(model_str)
                compute_type = get_compute_type(model_id)
            except ValueError as e:
                logger.error(f"❌ Invalid model ID {model_str}: {e}")
                raise HTTPException(
                    status_code=400,
                    detail=error_envelope(
                        code=ErrorCode.INVALID_MODEL,
                        message=f"Invalid model ID: {model_str}",
                        source="edge",
                        retryable=False,
                        data={"model_id": model_str},
                    ),
                )

            slot_ctx = SlotContext(
                gateway_id=gateway_id,
                model_str=model_str,
                request_id=request_id,
                endpoint_category=endpoint_category,
                compute_type=compute_type,
            )

            if gateway_id:
                _ = slot_ctx.try_reserve(max_concurrent_requests=1000)
            else:
                logger.warning(
                    f"⚠️ Edge slot tracking DISABLED (gateway_id=None) for "
                    f"model={model_str}, req={request_id[:8]}"
                )

            # Step 4: Forward to gateway
            stream = original_request.get("stream", False)

            # Embeddings and rerank are always non-streaming
            if endpoint in ("/v1/embeddings", "/v1/rerank"):
                stream = False

            if stream:
                from .error_parsing import create_gateway_http_exception

                try:
                    stream_iter = forward_to_gateway_streaming(
                        sock_path,
                        http_url,
                        original_request,
                        request_id,
                        explicit_timeout,
                        endpoint,
                    )
                    first_chunk = await stream_iter.__anext__()
                except httpx.HTTPStatusError as e:
                    slot_ctx.release()
                    request_store.complete(request_id)
                    logger.error(
                        f"Gateway HTTP error before stream: {e.response.status_code}",
                        extra={"request_id": request_id},
                    )
                    raise create_gateway_http_exception(e, source="edge")
                except StopAsyncIteration:
                    slot_ctx.release()
                    request_store.complete(request_id)
                    model_name = original_request.get("model", "unknown")
                    logger.error(
                        f"❌ Gateway returned empty stream for {model_name} "
                        f"(request={request_id[:8]})"
                    )
                    raise HTTPException(
                        status_code=502,
                        detail=error_envelope(
                            code=ErrorCode.UNEXPECTED_ERROR,
                            message="Gateway returned empty stream",
                            source="edge",
                            retryable=True,
                            data={
                                "request_id": request_id,
                                "model": model_name,
                            },
                        ),
                    )

                async def stream_generator_with_first():
                    try:
                        yield first_chunk
                        async for chunk in stream_iter:
                            if request_store.is_cancelled(request_id):
                                logger.info(f"Stream cancelled: {request_id[:8]}...")
                                break
                            yield chunk
                    except asyncio.CancelledError:
                        logger.info(f"Stream task cancelled: {request_id[:8]}...")
                        raise
                    except httpx.RequestError:
                        logger.exception(
                            "Connection error to gateway",
                            extra={"request_id": request_id},
                        )
                        yield (
                            json.dumps(
                                {
                                    "signal": "error",
                                    "payload": {"message": "Gateway connection error"},
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        ).encode("utf-8")
                    finally:
                        request_store.complete(request_id)
                        slot_ctx.release()

                return StreamingResponse(
                    stream_generator_with_first(),
                    media_type="application/newline-delimited-json",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )
            else:
                from .error_parsing import create_gateway_http_exception

                try:
                    result = await forward_to_gateway_nonstreaming(
                        sock_path,
                        http_url,
                        original_request,
                        request_id,
                        explicit_timeout,
                        endpoint,
                    )
                    request_store.complete(request_id)
                    slot_ctx.release()
                    return JSONResponse(content=result)
                except asyncio.CancelledError:
                    request_store.complete(request_id)
                    slot_ctx.release()
                    logger.info(
                        f"Gateway request cancelled: {request_id[:8]}...",
                        extra={"request_id": request_id},
                    )
                    raise
                except httpx.HTTPStatusError as e:
                    request_store.complete(request_id)
                    slot_ctx.release()
                    logger.error(
                        f"Gateway HTTP error: {e.response.status_code}",
                        extra={"request_id": request_id},
                    )
                    raise create_gateway_http_exception(e, source="edge")
                except httpx.RequestError as e:
                    request_store.complete(request_id)
                    slot_ctx.release()
                    logger.exception(
                        "Connection error to gateway",
                        extra={"request_id": request_id},
                    )
                    raise HTTPException(
                        status_code=get_http_status(ErrorCode.GATEWAY_DISCONNECTED),
                        detail=error_envelope(
                            code=ErrorCode.GATEWAY_DISCONNECTED,
                            message="Gateway connection error",
                            source="edge",
                            retryable=True,
                            data={"error": str(e)},
                        ),
                    )

        except HTTPException:
            request_store.complete(request_id)
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            request_store.complete(request_id)
            logger.exception(
                "Federation inference error",
                extra={"request_id": request_id},
            )
            raise HTTPException(status_code=500, detail="Internal server error")

    return router
