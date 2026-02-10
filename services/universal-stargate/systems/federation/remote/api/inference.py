"""
Federation inference endpoint for Remote mode.

Accepts forwarded requests from Master and routes to local Gateway.

INVARIANT: register(request_id) BEFORE first_await (body parsing)
"""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from model_id import ModelId, get_compute_type
from universal_logging import get_logger
from universal_protocol import ErrorCode, error_envelope, get_http_status

from src.core.streaming.ndjson_framing import iter_ndjson_lines_bytes

from ...common.config import FederationConfig
from ...common.types import HEADER_REQUEST_ID
from .request_store import ActiveRequestStore
from .slot_management import SlotContext

logger = get_logger(__name__)

# Deferred import to avoid circular dependency at module load
_derive_endpoint_category = None


def _get_endpoint_category(endpoint_path: str) -> str:
    """
    Get endpoint category string from path.

    Uses derive_endpoint_category with deferred import to avoid
    circular dependency.

    Returns:
        "generation" or "embedding"

    Raises:
        ValueError: If endpoint path is unknown/unsupported
    """
    global _derive_endpoint_category
    if _derive_endpoint_category is None:
        from systems.proxy.core.endpoint_category import derive_endpoint_category

        _derive_endpoint_category = derive_endpoint_category

    # Let ValueError propagate - caller must handle
    return _derive_endpoint_category(path=endpoint_path).value


def _resolve_gateway_connection(
    config: FederationConfig, gateway_socket_path: str | None, gateway_url: str | None
) -> tuple[str | None, str | None]:
    """
    Resolve gateway connection details (socket path OR HTTP URL).

    Translates container paths (/sockets/*) to host paths (/tmp/universal-sockets/*)
    to match Docker volume mount: /tmp/universal-sockets:/sockets

    Args:
        config: Federation configuration
        gateway_socket_path: Explicit socket path override (Edge/Master modes)
        gateway_url: Explicit HTTP URL (Edge/Master modes)

    Returns:
        Tuple of (socket_path, http_url) - exactly one will be non-None

    Raises:
        ValueError: If no connection method can be resolved
    """
    socket_path = None
    http_url = None

    # Resolve connection from config or parameters
    # Priority: explicit params > config.local_edge (Remote mode)
    if gateway_socket_path:
        socket_path = gateway_socket_path
    elif gateway_url:
        http_url = gateway_url
    elif config.local_edge:
        # Remote mode: local_edge points to Edge Stargate (which forwards to Gateway)
        socket_path = config.local_edge.socket_path

    # Validate at least one connection method is available
    if not socket_path and not http_url:
        raise ValueError(
            "Gateway connection (socket or URL) not configured for inference"
        )

    # Convert container socket paths to host paths if using sockets
    if socket_path and socket_path.startswith("/sockets/"):
        socket_name = socket_path.split("/")[-1]
        socket_path = f"/tmp/universal-sockets/{socket_name}"
        logger.debug(
            "Translated container socket: /sockets/%s -> %s",
            socket_name,
            socket_path,
        )

    return socket_path, http_url


def _extract_structured_detail(response: httpx.Response) -> dict | str:
    """Extract structured error detail from an HTTP error response.

    Preserves canonical error envelopes end-to-end so upstream routing/capacity
    logic can inspect code, retryable, etc. without heuristic re-mapping.

    Handles streaming responses where body may not have been read yet
    (raises httpx.ResponseNotRead).

    Returns:
        Parsed dict if response contains valid JSON dict, else raw text
    """
    try:
        payload = response.json()
        if isinstance(payload, dict):
            # Canonical envelope or FastAPI {"detail": {...}} — preserve as dict
            if "detail" in payload and isinstance(payload["detail"], dict):
                return payload["detail"]
            if "code" in payload or "message" in payload:
                return payload
            return payload
    except Exception:
        pass
    try:
        return response.text[:1000]
    except Exception:
        # Streaming response with unread body (httpx.ResponseNotRead)
        return f"HTTP {response.status_code} (body not available)"


async def forward_to_gateway_streaming(
    socket_path: str | None,
    http_url: str | None,
    request_body: dict[str, Any],
    request_id: str,
    read_timeout: float = 300.0,
    endpoint: str = "/v1/chat/completions",
) -> AsyncIterator[bytes]:
    """
    Forward streaming inference request to local Gateway via Unix socket or HTTP.

    Yields NDJSON lines transparently without buffering or parsing.

    INVARIANT: ¬buffer ∧ ¬parse ∧ ¬modify (pure passthrough)

    Args:
        socket_path: Unix socket path to gateway (if socket-based)
        http_url: HTTP URL to gateway (if HTTP-based)
        request_body: OpenAI-compatible request (model, messages, stream=True, etc.)
        request_id: Request ID for tracing
        read_timeout: HTTP read timeout in seconds (default: 300.0)
        endpoint: Gateway endpoint path (default: /v1/chat/completions)

    Yields:
        Raw bytes from SSE stream

    Raises:
        httpx.HTTPStatusError: On 4xx/5xx response
        httpx.RequestError: On connection failure
    """
    # Determine endpoint and transport
    if socket_path:
        gateway_url = "http://gateway"
        transport = httpx.AsyncHTTPTransport(uds=socket_path)
    elif http_url:
        gateway_url = http_url.rstrip("/")
        transport = None
    else:
        raise ValueError("Either socket_path or http_url must be provided")

    inference_endpoint = f"{gateway_url}{endpoint}"

    async with httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(connect=10.0, read=read_timeout, write=10.0, pool=10.0),
    ) as client:
        conn_type = "socket" if socket_path else "HTTP"
        logger.debug(
            f"Forwarding streaming request to Gateway via {conn_type}",
            extra={"request_id": request_id},
        )

        async with client.stream(
            "POST",
            inference_endpoint,
            json=request_body,
            headers={
                "Content-Type": "application/json",
                "X-Request-ID": request_id,
                "X-Request-Timeout": str(read_timeout),
            },
        ) as response:
            if response.status_code >= 400:
                error_body = await response.aread()
                error_preview = error_body.decode()[:500]
                raise httpx.HTTPStatusError(
                    f"Gateway returned {response.status_code}: {error_preview}",
                    request=response.request,
                    response=response,
                )

            # Preserve NDJSON framing without decode/encode overhead.
            async for framed_line in iter_ndjson_lines_bytes(response):
                yield framed_line


async def forward_to_gateway_nonstreaming(
    socket_path: str | None,
    http_url: str | None,
    request_body: dict[str, Any],
    request_id: str,
    read_timeout: float = 300.0,
    endpoint: str = "/v1/chat/completions",
) -> dict[str, Any]:
    """
    Forward non-streaming inference request to local Gateway via Unix socket or HTTP.

    INVARIANT: Pure passthrough - Gateway response returned directly

    Args:
        socket_path: Unix socket path to gateway (if socket-based)
        http_url: HTTP URL to gateway (if HTTP-based)
        request_body: OpenAI-compatible request (model, messages, stream=False, etc.)
        request_id: Request ID for tracing
        read_timeout: HTTP read timeout in seconds (default: 300.0)
        endpoint: Gateway endpoint path (default: /v1/chat/completions)

    Returns:
        Gateway response (OpenAI-compatible chat completion response)

    Raises:
        httpx.HTTPStatusError: On 4xx/5xx response
        httpx.RequestError: On connection failure
    """
    # Determine endpoint and transport
    if socket_path:
        gateway_url = "http://gateway"
        transport = httpx.AsyncHTTPTransport(uds=socket_path)
    elif http_url:
        gateway_url = http_url.rstrip("/")
        transport = None
    else:
        raise ValueError("Either socket_path or http_url must be provided")

    inference_endpoint = f"{gateway_url}{endpoint}"

    async with httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(connect=10.0, read=read_timeout, write=10.0, pool=10.0),
    ) as client:
        conn_type = "socket" if socket_path else "HTTP"
        logger.debug(
            f"Forwarding non-streaming request to Gateway via {conn_type}",
            extra={"request_id": request_id},
        )

        response = await client.post(
            inference_endpoint,
            json=request_body,
            headers={
                "Content-Type": "application/json",
                "X-Request-ID": request_id,
                "X-Request-Timeout": str(read_timeout),
            },
        )
        _ = response.raise_for_status()
        return response.json()


def create_inference_router(
    config: FederationConfig,
    request_store: ActiveRequestStore,
    gateway_socket_path: str | None = None,
    gateway_url: str | None = None,
    local_edge_client=None,  # For relay forwarding to Edge
    gateway_id: str | None = None,  # For slot reservation (Edge mode)
) -> APIRouter:
    """
    Create federation inference router.

    Args:
        config: Federation configuration
        request_store: Shared request store for tracking/cancellation
        gateway_socket_path: Explicit socket path override (Edge/Master modes)
        gateway_url: Explicit HTTP URL (Edge/Master modes)
        local_edge_client: Optional LocalEdgeClient for relay topology forwarding
        gateway_id: Gateway ID for slot reservation (Edge mode)

    Endpoints:
    - POST /api/v1/federation/inference - Forward to local Gateway or Edge
    """
    router = APIRouter(prefix="/api/v1/federation", tags=["federation"])

    @router.post("/inference")
    async def federation_inference(request: Request):
        """
        Handle forwarded inference request from Master.

        INVARIANT: register BEFORE first_await

        Relay Topology (Remote with local_edge):
            Remote → Edge (via Unix socket) → Gateway (inside Edge container)

        Direct Topology (Edge/Master with Gateway):
            Edge/Master → Gateway (via direct connection)

        Request body format (from Master's FederatedRequestForwarder):
        {
            "request": { /* original OpenAI request */ },
            "federation": {
                "source_stargate": "io",
                "request_id": "...",
                "hop_count": 0,
                "max_hops": 3,
                "target_gateway": "jupiter/gateway"
            }
        }

        Sequence:
        1. Extract request_id from header (sync)
        2. Register in store (sync) - BEFORE any await
        3. Parse body (await)
        4. Forward to Edge or Gateway
        """
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
                    # Edge exposes same /api/v1/federation/inference endpoint
                    # Forward with federation auth + request headers
                    headers = {
                        "X-Request-ID": request_id,
                        "Content-Type": "application/json",
                    }
                    # Add federation auth headers for Edge authentication
                    if config and config.stargate_id:
                        headers["X-Federation-Source"] = config.stargate_id
                        headers["X-Federation-Key"] = local_edge_client._config.api_key

                    if is_streaming:
                        # Streaming: Stream chunks immediately (no buffering)
                        # Persistent client keeps connection alive
                        # Placeholder URL - socket determines connection
                        client = httpx.AsyncClient(
                            transport=httpx.AsyncHTTPTransport(
                                uds=local_edge_client._config.socket_path
                            ),
                            timeout=1800.0,
                        )

                        async def stream_from_edge():
                            """Stream chunks from Edge immediately without buffering."""
                            try:
                                async with client.stream(
                                    "POST",
                                    "http://edge/api/v1/federation/inference",
                                    json=body,
                                    headers=headers,
                                ) as edge_response:
                                    edge_response.raise_for_status()

                                    # NDJSON lines without decode/encode.
                                    async for framed_line in iter_ndjson_lines_bytes(
                                        edge_response
                                    ):
                                        yield framed_line
                            finally:
                                # Close client after stream completes
                                await client.aclose()

                        # Collect headers before starting stream
                        # Note: edge_response.headers not available outside context;
                        # use reasonable defaults
                        return StreamingResponse(
                            stream_from_edge(),
                            media_type="application/newline-delimited-json",
                            headers={
                                "Cache-Control": "no-cache",
                                "Connection": "keep-alive",
                                "X-Accel-Buffering": "no",
                            },
                        )
                    else:
                        # Non-streaming: Regular request/response
                        # Placeholder URL - socket determines connection
                        async with httpx.AsyncClient(
                            transport=httpx.AsyncHTTPTransport(
                                uds=local_edge_client._config.socket_path
                            ),
                            timeout=1800.0,
                        ) as client:
                            edge_response = await client.post(
                                "http://edge/api/v1/federation/inference",
                                json=body,
                                headers=headers,
                            )

                            edge_response.raise_for_status()

                            # JSON response (non-streaming)
                            return JSONResponse(
                                content=edge_response.json(),
                                status_code=edge_response.status_code,
                            )
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
                    detail = _extract_structured_detail(e.response)
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

            # Direct topology: Connect to Gateway directly
            # Resolve gateway connection (socket or HTTP)
            try:
                sock_path, http_url = _resolve_gateway_connection(
                    config, gateway_socket_path, gateway_url
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

            # Extract timeout hint from federation metadata
            hints = federation.get("hints", {})
            read_timeout = float(hints.get("timeout", 300.0))

            if read_timeout != 300.0:
                logger.debug(
                    f"Using timeout hint: {read_timeout}s",
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

            # Slot reservation for Edge capacity enforcement
            # Parse model and determine capacity key BEFORE creating SlotContext
            model_str = original_request.get("model", "")

            # Determine endpoint category (raises ValueError if unknown)
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

            # Parse model ID and get compute type
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

            # Create SlotContext with all required params
            slot_ctx = SlotContext(
                gateway_id=gateway_id,
                model_str=model_str,
                request_id=request_id,
                endpoint_category=endpoint_category,
                compute_type=compute_type,
            )

            # Capacity gating is handled by Gateway's FifoCapacityGate
            # (from parallel_slots). Edge just tracks for cleanup.
            if gateway_id:
                _ = slot_ctx.try_reserve(max_concurrent_requests=1000)
            else:
                logger.warning(
                    f"⚠️ Edge slot tracking DISABLED (gateway_id=None) for "
                    f"model={model_str}, req={request_id[:8]}"
                )

            # Step 4: Forward to gateway
            stream = original_request.get("stream", False)

            # Embeddings are always non-streaming
            if endpoint == "/v1/embeddings":
                stream = False

            if stream:
                from .error_parsing import create_gateway_http_exception

                # Peek first chunk BEFORE returning StreamingResponse
                # Catches HTTP errors synchronously (Gateway errors before streaming)
                try:
                    stream_iter = forward_to_gateway_streaming(
                        sock_path,
                        http_url,
                        original_request,
                        request_id,
                        read_timeout,
                        endpoint,
                    )
                    first_chunk = await stream_iter.__anext__()
                except httpx.HTTPStatusError as e:
                    # Gateway returned error before streaming - return HTTP error
                    slot_ctx.release()
                    request_store.complete(request_id)
                    logger.error(
                        f"Gateway HTTP error before stream: {e.response.status_code}",
                        extra={"request_id": request_id},
                    )
                    raise create_gateway_http_exception(e, source="edge")
                except StopAsyncIteration:
                    # Empty stream from Gateway - this is an error condition
                    # Model is reliable and should always yield content
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
                    """Yield first chunk then continue iteration."""
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
                        # Mid-stream connection error - yield NDJSON error frame
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
                        read_timeout,
                        endpoint,
                    )
                    request_store.complete(request_id)
                    slot_ctx.release()
                    # Return Gateway response directly (pure passthrough)
                    return JSONResponse(content=result)
                except asyncio.CancelledError:
                    # Request was cancelled via WebSocket
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
            # Don't complete - leave as cancelled
            raise
        except Exception:
            request_store.complete(request_id)
            logger.exception(
                "Federation inference error",
                extra={"request_id": request_id},
            )
            raise HTTPException(status_code=500, detail="Internal server error")

    return router
