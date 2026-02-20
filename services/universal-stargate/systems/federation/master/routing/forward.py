"""
Request forwarding to federated gateways.

INVARIANT: ∀ federated request: uses FederatedRequestForwarder
CRITICAL: Separate methods for streaming vs non-streaming
          (async generators can't be returned)
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import HTTPException
from model_id import ModelId
from universal_logging import get_logger

from src.core.streaming.ndjson_framing import iter_ndjson_lines_bytes

from ...common.config import FederationConfig
from ...common.types import (
    HEADER_FEDERATION_HOP_COUNT,
    HEADER_FEDERATION_KEY,
    HEADER_FEDERATION_SOURCE,
    HEADER_REQUEST_ID,
    FederatedGateway,
)

logger = get_logger(__name__)


class FederatedRequestForwarder:
    """
    Forwards requests to federated gateways via Remote Stargates.

    INVARIANT: SSE forwarding is transparent (no buffering, parsing, modification)

    Lifecycle:
      1. Create forwarder with config
      2. Use forward_request() or forward_request_stream()
      3. Call await forwarder.close() on shutdown
    """

    def __init__(self, config: FederationConfig, event_bus: Any | None = None):
        self._config = config
        self._event_bus = event_bus

        # HTTP client with connection pooling (TCP)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0),
            limits=httpx.Limits(
                max_connections=config.http_pool.max_connections,
                max_keepalive_connections=config.http_pool.max_keepalive_connections,
            ),
        )

        # Unix socket HTTP clients by socket path
        self._unix_clients: dict[str, httpx.AsyncClient] = {}

        # API keys by stargate_id
        self._api_keys: dict[str, str] = {
            r.stargate_id: r.api_key for r in config.remotes
        }

        # Add local_edge API key if configured
        if config.local_edge:
            self._api_keys[config.local_edge.stargate_id] = config.local_edge.api_key

    async def close(self) -> None:
        """
        Close HTTP client. Must be called on shutdown.

        Typically called in main shutdown sequence or __aexit__.
        """
        await self._client.aclose()
        for client in self._unix_clients.values():
            await client.aclose()
        self._unix_clients.clear()
        logger.debug("FederatedRequestForwarder closed")

    def _get_client_for_url(self, url: str) -> httpx.AsyncClient:
        """Get appropriate HTTP client for URL (TCP or Unix socket)."""
        if url.startswith("unix://"):
            socket_path = url[7:]  # Strip "unix://"
            if socket_path not in self._unix_clients:
                transport = httpx.AsyncHTTPTransport(uds=socket_path)
                self._unix_clients[socket_path] = httpx.AsyncClient(
                    transport=transport,
                    base_url="http://localhost",  # Host ignored for UDS
                    timeout=httpx.Timeout(
                        connect=10.0, read=300.0, write=10.0, pool=10.0
                    ),
                )
            return self._unix_clients[socket_path]
        return self._client

    def _get_api_key(self, remote_stargate_id: str) -> str:
        """
        Get API key for remote stargate (fail-fast).

        Args:
            remote_stargate_id: Remote Stargate ID

        Returns:
            API key for the remote

        Raises:
            KeyError: If remote_stargate_id not configured
        """
        if remote_stargate_id not in self._api_keys:
            raise KeyError(
                f"No API key configured for remote stargate: {remote_stargate_id}. "
                f"Check config.remotes or config.local_edge."
            )
        return self._api_keys[remote_stargate_id]

    def _build_headers(
        self,
        remote_stargate_id: str,
        hop_count: int,
        request_id: str,
    ) -> dict[str, str]:
        """Build federation headers for request."""
        return {
            HEADER_FEDERATION_SOURCE: self._config.stargate_id,
            HEADER_FEDERATION_KEY: self._get_api_key(remote_stargate_id),
            HEADER_FEDERATION_HOP_COUNT: str(hop_count),
            HEADER_REQUEST_ID: request_id,
            "Content-Type": "application/json",
        }

    def _build_body(
        self,
        request_body: dict[str, Any],
        gateway: FederatedGateway,
        request_id: str,
        hop_count: int,
        hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build request body with federation metadata and transformation hints."""
        federation = {
            "source_stargate": self._config.stargate_id,
            "request_id": request_id,
            "hop_count": hop_count,
            "max_hops": self._config.max_hops,
            "target_gateway": gateway.gateway_id,
        }
        if hints:
            federation["hints"] = hints

        return {
            "request": request_body,
            "federation": federation,
        }

    async def forward_request(
        self,
        gateway: FederatedGateway,
        request_body: dict[str, Any],
        hop_count: int,
        request_id: str,
        hints: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """
        Forward non-streaming request to federated gateway.

        Routes via the Remote Stargate that owns the gateway.

        Args:
            gateway: Target federated gateway
            request_body: Request payload
            hop_count: Federation hop count
            request_id: Proxy request ID for tracking (sent as X-Correlation-ID header)
            hints: Optional transformation hints

        Returns:
            httpx.Response with complete body

        Raises:
            httpx.HTTPStatusError: On 4xx/5xx response
        """
        client = self._get_client_for_url(gateway.remote_stargate_url)

        # For Unix socket, use path-only endpoint
        if gateway.remote_stargate_url.startswith("unix://"):
            endpoint = "/api/v1/federation/inference"
        else:
            endpoint = f"{gateway.remote_stargate_url}/api/v1/federation/inference"

        headers = self._build_headers(gateway.remote_stargate_id, hop_count, request_id)
        body = self._build_body(request_body, gateway, request_id, hop_count, hints)

        # Extract model_id from request body for event
        model_id = request_body.get("model", "unknown")

        # Emit routing delegated event
        if self._event_bus:
            import asyncio

            from src.scheduling.events import FederationRoutingDelegated

            asyncio.create_task(
                self._event_bus.publish_async_nowait(
                    FederationRoutingDelegated(
                        request_id=request_id,
                        target_remote=gateway.remote_stargate_id,
                        model_id=model_id,
                        reason=f"Model routed to {gateway.gateway_id}",
                    )
                )
            )

        logger.debug(
            f"Forwarding to {gateway.gateway_id} via {gateway.remote_stargate_id}",
            extra={"gateway_id": gateway.gateway_id, "request_id": request_id},
        )

        # Only override client-level timeout if explicitly configured
        timeout_kwargs: dict[str, Any] = {}
        if hints and "timeout" in hints:
            timeout_kwargs["timeout"] = float(hints["timeout"])
            logger.debug("Using timeout hint: %ss", hints["timeout"])

        response = await client.post(
            endpoint, json=body, headers=headers, **timeout_kwargs
        )
        response.raise_for_status()
        return response

    async def forward_request_stream(
        self,
        gateway: FederatedGateway,
        request_body: dict[str, Any],
        hop_count: int,
        request_id: str,
        hints: dict[str, Any] | None = None,
    ) -> AsyncIterator[bytes]:
        """
        Forward streaming request to federated gateway.

        Yields NDJSON lines transparently without buffering or parsing.

        INVARIANT: ¬buffer ∧ ¬parse ∧ ¬modify

        Args:
            gateway: Target federated gateway
            request_body: Request payload
            hop_count: Federation hop count
            request_id: Proxy request ID for tracking
            hints: Optional transformation hints

        Yields:
            Raw bytes from SSE stream

        Raises:
            httpx.HTTPStatusError: On 4xx/5xx response
        """
        client = self._get_client_for_url(gateway.remote_stargate_url)

        # For Unix socket, use path-only endpoint
        if gateway.remote_stargate_url.startswith("unix://"):
            endpoint = "/api/v1/federation/inference"
        else:
            endpoint = f"{gateway.remote_stargate_url}/api/v1/federation/inference"

        headers = self._build_headers(gateway.remote_stargate_id, hop_count, request_id)
        body = self._build_body(request_body, gateway, request_id, hop_count, hints)

        # Extract model_id from request body for event
        model_id = request_body.get("model", "unknown")

        # Emit routing delegated event
        if self._event_bus:
            import asyncio

            from src.scheduling.events import FederationRoutingDelegated

            asyncio.create_task(
                self._event_bus.publish_async_nowait(
                    FederationRoutingDelegated(
                        request_id=request_id,
                        target_remote=gateway.remote_stargate_id,
                        model_id=model_id,
                        reason=f"Model routed to {gateway.gateway_id}",
                    )
                )
            )

        logger.debug(
            f"Forwarding stream to {gateway.gateway_id} "
            f"via {gateway.remote_stargate_id}",
            extra={
                "gateway_id": gateway.gateway_id,
                "request_id": request_id,
            },
        )

        # Only override client-level timeout if explicitly configured
        timeout_kwargs: dict[str, Any] = {}
        if hints and "timeout" in hints:
            timeout_kwargs["timeout"] = float(hints["timeout"])

        async with client.stream(
            "POST", endpoint, json=body, headers=headers, **timeout_kwargs
        ) as response:
            if response.status_code >= 400:
                error_body = await response.aread()
                error_preview = error_body.decode()[:200]
                raise httpx.HTTPStatusError(
                    f"Remote returned {response.status_code}: {error_preview}",
                    request=response.request,
                    response=response,
                )

            # Preserve NDJSON framing without decode/encode overhead.
            async for framed_line in iter_ndjson_lines_bytes(response):
                yield framed_line

    async def forward_token_request(
        self,
        gateway: FederatedGateway,
        token_payload: dict[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        """
        Forward token count request to Remote Stargate.

        Uses federation auth headers to access /api/v1/federation/tokens/count.

        Args:
            gateway: Target federated gateway (provides remote_stargate_url)
            token_payload: Token counting payload (model, messages/prompt)
            request_id: Proxy request ID for tracing

        Returns:
            Token count response (token_count, context_limit, max_generation_tokens)

        Raises:
            httpx.HTTPStatusError: On remote error (4xx/5xx)
            httpx.RequestError: On connection failure
        """
        client = self._get_client_for_url(gateway.remote_stargate_url)

        # For Unix socket, use path-only endpoint
        if gateway.remote_stargate_url.startswith("unix://"):
            endpoint = "/api/v1/federation/tokens/count"
        else:
            endpoint = f"{gateway.remote_stargate_url}/api/v1/federation/tokens/count"

        headers = self._build_headers(
            gateway.remote_stargate_id,
            hop_count=0,  # Token counting doesn't use hop semantics
            request_id=request_id,
        )

        logger.debug(
            f"Token count request to {gateway.remote_stargate_id}",
            extra={
                "gateway_id": gateway.gateway_id,
                "request_id": request_id,
            },
        )

        response = await client.post(endpoint, json=token_payload, headers=headers)
        response.raise_for_status()
        return response.json()

    async def forward_model_load_request(
        self,
        gateway: FederatedGateway,
        model_id: ModelId,  # Changed: ModelId object, not str
        sticky: bool,
        request_id: str,
    ) -> dict[str, Any]:
        """
        Forward model load request to Remote.

        CONTRACT:
        - Returns structured dict with status, status_code, message
        - Does NOT raise HTTPException (orchestrator decides retry)
        - Serializes ModelId to string for HTTP request body

        Args:
            gateway: Target gateway
            model_id: Model to load (ModelId object)
            sticky: Sticky routing flag
            request_id: Proxy request ID for tracking

        Returns:
            Structured response dict with status, status_code, message
        """
        try:
            client = self._get_client_for_url(gateway.remote_stargate_url)

            # For Unix socket, use path-only endpoint
            if gateway.remote_stargate_url.startswith("unix://"):
                endpoint = "/api/v1/federation/models/load"
            else:
                endpoint = (
                    f"{gateway.remote_stargate_url}/api/v1/federation/models/load"
                )

            headers = self._build_headers(
                gateway.remote_stargate_id,
                hop_count=0,
                request_id=request_id,
            )

            payload = {
                "model_id": str(model_id),  # Serialize here
                "sticky": sticky,
            }

            logger.info(
                f"🔄 Model load request to {gateway.remote_stargate_id}: {model_id}",
                extra={
                    "gateway_id": gateway.gateway_id,
                    "request_id": request_id,
                },
            )

            response = await client.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(connect=10.0, read=175.0, write=10.0, pool=10.0),
            )

            if response.is_success:
                body = response.json()
                # Remote API returns HTTP 200 with status="failed" for
                # application-level failures (e.g., insufficient VRAM).
                # Map to 503 so orchestrator treats as retryable, not 4xx.
                if body.get("status") == "failed":
                    return {
                        "status": "failed",
                        "status_code": 503,
                        "message": body.get("message", "Remote load failed"),
                    }
                return {
                    "status": "ok",
                    "status_code": response.status_code,
                    "message": "Model loaded successfully",
                    **body,
                }
            else:
                return {
                    "status": "failed",
                    "status_code": response.status_code,
                    "message": response.text[:500],
                }

        except httpx.TimeoutException:
            # Let this propagate - orchestrator handles retry
            raise

        except httpx.RequestError:
            # Let this propagate - orchestrator handles retry
            raise

    async def forward_model_unload_request(
        self,
        gateway: FederatedGateway,
        model_id: ModelId,
        request_id: str,
    ) -> dict[str, Any]:
        """
        Forward model unload request to Remote.

        CONTRACT:
        - Returns structured dict with status, status_code, message
        - Does NOT raise HTTPException (orchestrator decides retry)

        Args:
            gateway: Target gateway
            model_id: Model to unload (ModelId object)
            request_id: Proxy request ID for tracking

        Returns:
            Structured response dict with status, status_code, message
        """
        try:
            client = self._get_client_for_url(gateway.remote_stargate_url)

            if gateway.remote_stargate_url.startswith("unix://"):
                endpoint = "/api/v1/federation/models/unload"
            else:
                endpoint = (
                    f"{gateway.remote_stargate_url}/api/v1/federation/models/unload"
                )

            headers = self._build_headers(
                gateway.remote_stargate_id,
                hop_count=0,
                request_id=request_id,
            )

            payload = {"model_id": str(model_id)}

            logger.info(
                f"🗑️ Model unload request to {gateway.remote_stargate_id}: {model_id}",
                extra={
                    "gateway_id": gateway.gateway_id,
                    "request_id": request_id,
                },
            )

            response = await client.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
            )

            if response.is_success:
                return {
                    "status": "ok",
                    "status_code": response.status_code,
                    "message": "Model unloaded successfully",
                    **response.json(),
                }
            else:
                return {
                    "status": "failed",
                    "status_code": response.status_code,
                    "message": response.text[:500],
                }

        except httpx.TimeoutException:
            raise

        except httpx.RequestError:
            raise

    async def forward_embedding_request(
        self,
        gateway: FederatedGateway,
        request_body: dict,
        request_id: str | None = None,
    ) -> dict:
        """
        Forward embedding request to federated gateway.

        Uses canonical federation envelope with proper headers and error handling.

        Args:
            gateway: Target gateway
            request_body: Embedding request (model, input)
            request_id: Proxy request ID for tracking. If None, generates new UUID.

        Returns:
            Embedding response from gateway

        Raises:
            HTTPException: On gateway errors (preserves status + message)
        """
        client = self._get_client_for_url(gateway.remote_stargate_url)

        # Use path-only for Unix socket, full URL for HTTP
        if gateway.remote_stargate_url.startswith("unix://"):
            endpoint = "/api/v1/federation/inference"
        else:
            endpoint = f"{gateway.remote_stargate_url}/api/v1/federation/inference"

        # Generate request_id if not provided
        req_id = request_id or str(uuid.uuid4())
        hop_count = 1  # Embeddings don't do multi-hop

        # Use canonical header builder (includes proper API key lookup)
        headers = self._build_headers(
            gateway.remote_stargate_id,
            hop_count,
            req_id,
        )

        # Use canonical body builder with federation envelope
        body = self._build_body(
            request_body,
            gateway,
            req_id,
            hop_count,
            hints=None,
        )

        # Add embedding-specific endpoint hint
        body["federation"]["endpoint"] = "/v1/embeddings"

        logger.debug(
            f"Forwarding embedding request to {gateway.gateway_id} "
            f"via {gateway.remote_stargate_id}",
            extra={
                "gateway_id": gateway.gateway_id,
                "request_id": req_id,
            },
        )

        response = await client.post(
            endpoint,
            json=body,
            headers=headers,
            timeout=30.0,
        )

        # Handle errors with proper propagation (don't use raise_for_status)
        if not response.is_success:
            # Try to parse error response
            try:
                error_detail = response.json()
            except Exception:
                error_detail = {"message": response.text[:500]}

            logger.error(
                f"Federated embedding request failed: {response.status_code}",
                extra={
                    "request_id": req_id,
                    "gateway_id": gateway.gateway_id,
                    "error": error_detail,
                },
            )

            # Preserve remote status code and detail
            raise HTTPException(
                status_code=response.status_code,
                detail=error_detail,
            )

        return response.json()
