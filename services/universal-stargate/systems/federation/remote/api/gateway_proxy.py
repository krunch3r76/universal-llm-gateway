"""
Gateway management proxy for Edge Stargate.

Proxies Gateway management API endpoints for federated access.

INVARIANT: ∀ operation: forwards_downstream(operation) (proxy pattern)
INVARIANT: ¬∃ import from master/ (domain isolation)
INVARIANT: SSE streams passed through without buffering
"""

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from universal_logging import get_logger

logger = get_logger(__name__)

# Timeout for measurement operations (can take 30+ minutes)
MEASUREMENT_TIMEOUT = 1800.0  # 30 minutes


def create_gateway_proxy_router(
    gateway_socket_path: str | None = None,
    gateway_url: str | None = None,
) -> APIRouter:
    """
    Create router that proxies Gateway management endpoints.

    Edge mode: Proxies to local Gateway via socket/HTTP.

    Args:
        gateway_socket_path: Unix socket path to Gateway
        gateway_url: HTTP URL to Gateway

    Returns:
        APIRouter with /api/v1/federation/gateway/* endpoints
    """
    router = APIRouter(
        prefix="/api/v1/federation/gateway",
        tags=["federation-gateway-proxy"],
    )

    def _get_client() -> httpx.AsyncClient:
        """Get httpx client for Gateway connection."""
        if gateway_socket_path:
            transport = httpx.AsyncHTTPTransport(uds=gateway_socket_path)
            return httpx.AsyncClient(transport=transport, timeout=MEASUREMENT_TIMEOUT)
        elif gateway_url:
            return httpx.AsyncClient(base_url=gateway_url, timeout=MEASUREMENT_TIMEOUT)
        else:
            raise HTTPException(503, "Gateway connection not configured")

    def _build_gateway_endpoint(path: str) -> str:
        """
        Build Gateway endpoint URL from path.

        Args:
            path: API path (e.g., "/api/v1/jobs")

        Returns:
            Full endpoint URL for Gateway connection
        """
        if gateway_socket_path:
            return f"http://localhost{path}"
        return f"{gateway_url}{path}"

    def _convert_httpx_response(response: httpx.Response) -> Response:
        """
        Convert httpx Response to FastAPI Response.

        Preserves status code, headers, and body content.
        """
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
        )

    def _raise_gateway_connection_error(error: httpx.ConnectError) -> None:
        """
        Handle Gateway connection error.

        Logs error and raises HTTPException(503).
        """
        logger.error(f"Gateway connection failed: {error}", exc_info=True)
        raise HTTPException(503, "Gateway connection unavailable")

    # --- Jobs API ---

    @router.post("/jobs")
    async def create_job(request: Request) -> Response:
        """
        Proxy job creation to Gateway.

        POST /api/v1/federation/gateway/jobs → Gateway /api/v1/jobs
        """
        job_request = await request.json()

        logger.info(f"Proxying job creation: {job_request.get('model_id', 'unknown')}")

        async with _get_client() as client:
            try:
                endpoint = _build_gateway_endpoint("/api/v1/jobs")
                response = await client.post(endpoint, json=job_request)
                return _convert_httpx_response(response)
            except httpx.ConnectError as e:
                _raise_gateway_connection_error(e)

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: str) -> Response:
        """
        Proxy job status request to Gateway.

        GET /api/v1/federation/gateway/jobs/{id} → Gateway /api/v1/jobs/{id}
        """
        logger.debug(f"Proxying job status: {job_id}")

        async with _get_client() as client:
            try:
                endpoint = _build_gateway_endpoint(f"/api/v1/jobs/{job_id}")
                response = await client.get(endpoint)
                return _convert_httpx_response(response)
            except httpx.ConnectError as e:
                _raise_gateway_connection_error(e)

    @router.get("/jobs/{job_id}/logs")
    async def stream_job_logs(job_id: str) -> StreamingResponse:
        """
        Proxy job log streaming (SSE) to Gateway.

        GET /api/v1/federation/gateway/jobs/{id}/logs
        → Gateway /api/v1/jobs/{id}/logs

        CRITICAL: Must stream SSE events without buffering.
        """
        logger.info(f"Proxying job log stream: {job_id}")

        async def stream_generator():
            async with _get_client() as client:
                try:
                    endpoint = _build_gateway_endpoint(f"/api/v1/jobs/{job_id}/logs")

                    async with client.stream("GET", endpoint) as response:
                        if response.status_code != 200:
                            error_body = await response.aread()
                            logger.error(
                                f"Gateway returned {response.status_code}: {error_body}"
                            )
                            error_msg = (
                                f'data: {{"error": "Gateway returned '
                                f'{response.status_code}"}}\n\n'
                            )
                            yield error_msg
                            return

                        async for chunk in response.aiter_bytes():
                            yield chunk

                except httpx.ConnectError as e:
                    logger.error(f"Gateway connection failed: {e}", exc_info=True)
                    error_msg = 'data: {"error": "Gateway connection unavailable"}\n\n'
                    yield error_msg

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
    async def cancel_job(job_id: str) -> Response:
        """
        Proxy job cancellation to Gateway.

        DELETE /api/v1/federation/gateway/jobs/{id} → Gateway /api/v1/jobs/{id}
        """
        logger.info(f"Proxying job cancellation: {job_id}")

        async with _get_client() as client:
            try:
                endpoint = _build_gateway_endpoint(f"/api/v1/jobs/{job_id}")
                response = await client.delete(endpoint)
                return _convert_httpx_response(response)
            except httpx.ConnectError as e:
                _raise_gateway_connection_error(e)

    # --- Status API ---

    @router.get("/status/resources")
    async def get_resources() -> Response:
        """
        Proxy resource status to Gateway.

        GET /api/v1/federation/gateway/status/resources
        → Gateway /api/v1/status/resources
        """
        logger.debug("Proxying resource status")

        async with _get_client() as client:
            try:
                endpoint = _build_gateway_endpoint("/api/v1/status/resources")
                response = await client.get(endpoint, timeout=10.0)
                return _convert_httpx_response(response)
            except httpx.ConnectError as e:
                _raise_gateway_connection_error(e)

    # --- Models API ---

    @router.get("/models/{model_id}/config")
    async def get_model_config(model_id: str) -> Response:
        """
        Proxy model config fetch to Gateway.

        GET /api/v1/federation/gateway/models/{id}/config
        → Gateway /api/v1/models/{id}/config
        """
        logger.debug(f"Proxying model config: {model_id}")

        async with _get_client() as client:
            try:
                endpoint = _build_gateway_endpoint(f"/api/v1/models/{model_id}/config")
                response = await client.get(endpoint, timeout=10.0)
                return _convert_httpx_response(response)
            except httpx.ConnectError as e:
                _raise_gateway_connection_error(e)

    @router.post("/models")
    async def update_model(request: Request) -> Response:
        """
        Proxy model catalog update to Gateway.

        POST /api/v1/federation/gateway/models → Gateway /api/v1/models
        """
        catalog_request = await request.json()
        model_key = catalog_request.get("model_key", "unknown")

        logger.info(f"Proxying catalog update: {model_key}")

        async with _get_client() as client:
            try:
                endpoint = _build_gateway_endpoint("/api/v1/models")
                response = await client.post(
                    endpoint, json=catalog_request, timeout=30.0
                )
                return _convert_httpx_response(response)
            except httpx.ConnectError as e:
                _raise_gateway_connection_error(e)

    @router.delete("/models/{model_id}")
    async def unload_model(model_id: str) -> Response:
        """
        Proxy model unload to Gateway.

        DELETE /api/v1/federation/gateway/models/{id} → Gateway /api/v1/models/{id}
        """
        logger.info(f"Proxying model unload: {model_id}")

        async with _get_client() as client:
            try:
                endpoint = _build_gateway_endpoint(f"/api/v1/models/{model_id}")
                response = await client.delete(endpoint, timeout=60.0)
                return _convert_httpx_response(response)
            except httpx.ConnectError as e:
                _raise_gateway_connection_error(e)

    return router
