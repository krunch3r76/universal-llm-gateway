"""
Gateway management forwarding router for Master/Relay Stargates.

Forwards /gateway/* requests to local Edge Stargate.

INVARIANT: ∀ request: forward_to_edge(request) via Unix socket
INVARIANT: SSE streams passed through without buffering
INVARIANT: ∀ request: includes federation auth headers
           (X-Federation-Source, X-Federation-Key)
"""

import json

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from universal_logging import get_logger

from systems.federation.common.types import (
    HEADER_FEDERATION_KEY,
    HEADER_FEDERATION_SOURCE,
)

logger = get_logger(__name__)

# Timeout for measurement operations (can take 30+ minutes)
MEASUREMENT_TIMEOUT = 1800.0  # 30 minutes


def _create_sse_error(message: str) -> str:
    """
    Create SSE error event with proper JSON encoding.

    Args:
        message: Error message to send

    Returns:
        SSE-formatted error event (data: {...}\n\n)
    """
    error_data = {"error": message}
    return f"data: {json.dumps(error_data)}\n\n"


def create_gateway_forward_router(
    local_edge_socket_path: str | None = None,
    stargate_id: str | None = None,
    edge_api_key: str | None = None,
) -> APIRouter:
    """
    Create router that forwards Gateway requests to local Edge.

    Master/Relay mode: Forwards to Edge via Unix socket with federation auth.

    Args:
        local_edge_socket_path: Unix socket path to local Edge Stargate
        stargate_id: Master's stargate ID (for X-Federation-Source header)
        edge_api_key: Edge's API key (for X-Federation-Key header)

    Returns:
        APIRouter with /gateway/* endpoints
    """
    router = APIRouter(prefix="/gateway", tags=["gateway-forward"])

    if not local_edge_socket_path:
        # No local Edge configured - return empty router
        logger.debug("No local Edge socket configured - gateway forwarding disabled")
        return router

    # Federation auth headers for all Edge requests
    federation_headers = {}
    if stargate_id and edge_api_key:
        federation_headers[HEADER_FEDERATION_SOURCE] = stargate_id
        federation_headers[HEADER_FEDERATION_KEY] = edge_api_key
        logger.info(
            f"Gateway forwarding configured with federation auth: {stargate_id}"
        )
    else:
        logger.warning(
            "Gateway forwarding configured without federation auth "
            "(stargate_id or edge_api_key missing)"
        )

    def _get_client() -> httpx.AsyncClient:
        """Get httpx client for Edge connection."""
        transport = httpx.AsyncHTTPTransport(uds=local_edge_socket_path)
        return httpx.AsyncClient(transport=transport, timeout=MEASUREMENT_TIMEOUT)

    # --- Jobs API ---

    @router.post("/jobs")
    async def forward_create_job(request: Request) -> Response:
        """
        Forward job creation to Edge Stargate.

        POST /gateway/jobs → Edge /api/v1/federation/gateway/jobs
        """
        body = await request.json()

        model_id = body.get("model_id", "unknown")
        logger.info(f"Forwarding job creation to Edge: {model_id}")

        async with _get_client() as client:
            try:
                response = await client.post(
                    "http://localhost/api/v1/federation/gateway/jobs",
                    json=body,
                    headers=federation_headers,
                )

                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                )
            except httpx.ConnectError as e:
                logger.error(f"Edge connection failed: {e}", exc_info=True)
                raise HTTPException(503, "Edge connection unavailable")

    @router.get("/jobs/{job_id}")
    async def forward_get_job(job_id: str) -> Response:
        """
        Forward job status request to Edge Stargate.

        GET /gateway/jobs/{id} → Edge /api/v1/federation/gateway/jobs/{id}
        """
        logger.debug(f"Forwarding job status to Edge: {job_id}")

        async with _get_client() as client:
            try:
                response = await client.get(
                    f"http://localhost/api/v1/federation/gateway/jobs/{job_id}",
                    headers=federation_headers,
                )

                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                )
            except httpx.ConnectError as e:
                logger.error(f"Edge connection failed: {e}", exc_info=True)
                raise HTTPException(503, "Edge connection unavailable")

    @router.get("/jobs/{job_id}/logs")
    async def forward_stream_job_logs(job_id: str) -> StreamingResponse:
        """
        Forward job log streaming (SSE) to Edge Stargate.

        GET /gateway/jobs/{id}/logs →
            Edge /api/v1/federation/gateway/jobs/{id}/logs

        CRITICAL: Must stream SSE events without buffering.
        """
        logger.info(f"Forwarding job log stream to Edge: {job_id}")

        async def stream_generator():
            async with _get_client() as client:
                try:
                    async with client.stream(
                        "GET",
                        f"http://localhost/api/v1/federation/gateway/jobs/{job_id}/logs",
                        headers=federation_headers,
                    ) as response:
                        if response.status_code != 200:
                            error_body = await response.aread()
                            logger.error(
                                f"Edge returned {response.status_code}: {error_body}"
                            )
                            yield _create_sse_error(
                                f"Edge returned {response.status_code}"
                            )
                            return

                        async for chunk in response.aiter_bytes():
                            yield chunk

                except httpx.ConnectError as e:
                    logger.error(f"Edge connection failed: {e}", exc_info=True)
                    yield _create_sse_error("Edge connection unavailable")

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.delete("/jobs/{job_id}")
    async def forward_cancel_job(job_id: str) -> Response:
        """
        Forward job cancellation to Edge Stargate.

        DELETE /gateway/jobs/{id} → Edge /api/v1/federation/gateway/jobs/{id}
        """
        logger.info(f"Forwarding job cancellation to Edge: {job_id}")

        async with _get_client() as client:
            try:
                response = await client.delete(
                    f"http://localhost/api/v1/federation/gateway/jobs/{job_id}",
                    headers=federation_headers,
                )

                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                )
            except httpx.ConnectError as e:
                logger.error(f"Edge connection failed: {e}", exc_info=True)
                raise HTTPException(503, "Edge connection unavailable")

    # --- Status API ---

    @router.get("/status/resources")
    async def forward_get_resources() -> Response:
        """
        Forward resource status to Edge Stargate.

        GET /gateway/status/resources →
            Edge /api/v1/federation/gateway/status/resources
        """
        logger.debug("Forwarding resource status to Edge")

        async with _get_client() as client:
            try:
                response = await client.get(
                    "http://localhost/api/v1/federation/gateway/status/resources",
                    timeout=10.0,
                    headers=federation_headers,
                )

                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                )
            except httpx.ConnectError as e:
                logger.error(f"Edge connection failed: {e}", exc_info=True)
                raise HTTPException(503, "Edge connection unavailable")

    # --- Models API ---

    @router.get("/models/{model_id}/config")
    async def forward_get_model_config(model_id: str) -> Response:
        """
        Forward model config fetch to Edge Gateway.

        GET /gateway/models/{id}/config →
            Edge Stargate /gateway/api/v1/catalog/models/{id} →
            Gateway /api/v1/catalog/models/{id}

        Transforms Gateway's ModelEntryResponse into expected {config: {...}} format.
        """
        logger.debug(f"Forwarding model config to Edge Gateway: {model_id}")

        async with _get_client() as client:
            try:
                # Route through Edge's /gateway/* namespace (Edge strips prefix)
                response = await client.get(
                    f"http://localhost/gateway/api/v1/catalog/models/{model_id}",
                    timeout=10.0,
                    headers=federation_headers,
                )

                if response.status_code == 200:
                    # Transform ModelEntryResponse to {config: {...}} format
                    data = response.json()
                    # Reconstruct catalog entry format (remove model_id, rename schema)
                    catalog_entry = {
                        "schema": data.get("schema_name", data.get("schema")),
                        "metadata": data.get("metadata", {}),
                        "download": data.get("download", {}),
                        "loader": data.get("loader", {}),
                        "devices": data.get("devices", {}),
                    }
                    wrapped_response = {"config": catalog_entry}

                    return Response(
                        content=json.dumps(wrapped_response),
                        status_code=200,
                        headers={"content-type": "application/json"},
                    )

                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                )
            except httpx.ConnectError as e:
                logger.error(f"Edge connection failed: {e}", exc_info=True)
                raise HTTPException(503, "Edge connection unavailable")

    @router.post("/models")
    async def forward_update_model(request: Request) -> Response:
        """
        Forward model catalog update to Edge Stargate.

        POST /gateway/models → Edge /api/v1/federation/gateway/models

        INVARIANT: static=true is rejected (Gateway has read-only config mount)
        Static catalog writes must use CLI direct file writes.
        """
        body = await request.json()
        model_key = body.get("model_key", "unknown")

        # Reject static catalog updates (Gateway has read-only config mount)
        if body.get("static", False):
            logger.error(
                f"Rejected static catalog update via API: {model_key}. "
                "Static writes require CLI (host filesystem access)."
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "static_catalog_via_api_not_supported",
                    "message": (
                        "Static catalog updates cannot use API "
                        "(Gateway has read-only config). "
                        "Use CLI without --stargate flag, or use dynamic catalog."
                    ),
                },
            )

        logger.info(f"Forwarding catalog update to Edge: {model_key}")

        async with _get_client() as client:
            try:
                response = await client.post(
                    "http://localhost/api/v1/federation/gateway/models",
                    json=body,
                    timeout=30.0,
                    headers=federation_headers,
                )

                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                )
            except httpx.ConnectError as e:
                logger.error(f"Edge connection failed: {e}", exc_info=True)
                raise HTTPException(503, "Edge connection unavailable")

    @router.delete("/models/{model_id}")
    async def forward_unload_model(model_id: str) -> Response:
        """
        Forward model unload to Edge Stargate.

        DELETE /gateway/models/{id} →
            Edge /api/v1/federation/gateway/models/{id}
        """
        logger.info(f"Forwarding model unload to Edge: {model_id}")

        async with _get_client() as client:
            try:
                response = await client.delete(
                    f"http://localhost/api/v1/federation/gateway/models/{model_id}",
                    timeout=60.0,
                    headers=federation_headers,
                )

                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                )
            except httpx.ConnectError as e:
                logger.error(f"Edge connection failed: {e}", exc_info=True)
                raise HTTPException(503, "Edge connection unavailable")

    # --- Catalog Management ---

    @router.post("/catalog/reload")
    async def forward_reload_catalog() -> Response:
        """
        Forward catalog reload to Edge Gateway.

        POST /gateway/catalog/reload →
            Edge Stargate /gateway/api/v1/catalog/reload →
            Gateway /api/v1/catalog/reload

        Used after static catalog updates to refresh Gateway's in-memory catalog.
        """
        logger.info("Forwarding catalog reload to Edge Gateway")

        async with _get_client() as client:
            try:
                # Route through Edge's /gateway/* namespace (Edge strips prefix)
                response = await client.post(
                    "http://localhost/gateway/api/v1/catalog/reload",
                    timeout=10.0,
                    headers=federation_headers,
                )

                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                )
            except httpx.ConnectError as e:
                logger.error(f"Edge connection failed: {e}", exc_info=True)
                raise HTTPException(503, "Edge connection unavailable")

    logger.info(
        f"Gateway forwarding router configured for Edge: {local_edge_socket_path}"
    )
    return router
