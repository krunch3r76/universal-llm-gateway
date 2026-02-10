"""
Federation token counting API.

Proxies token count requests to local Gateway.
Available in REMOTE, MASTER, and EDGE modes.

INVARIANT: endpoint ∈ /api/v1/federation/* ⟹ covered by remote allowlist + auth
INVARIANT: Pure proxy - Master handles orchestration before calling this endpoint

STRUCTURAL ENFORCEMENT: This module does NOT import or use model_manager.
If you need orchestration, use federation/api/models.py instead.
"""

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from universal_logging import get_logger

from ...common.config import FederationConfig

logger = get_logger(__name__)


def _resolve_gateway_connection(
    config: FederationConfig, gateway_socket_path: str | None, gateway_url: str | None
) -> tuple[str | None, str | None]:
    """
    Resolve gateway connection details (socket path OR HTTP URL).

    Translates container paths (/sockets/*) to host paths (/tmp/universal-sockets/*)
    to match Docker volume mount: /tmp/universal-sockets:/sockets

    Args:
        config: Federation configuration
        gateway_socket_path: Explicit socket path (Edge/Master modes with Unix socket)
        gateway_url: Explicit HTTP URL (Edge/Master modes with HTTP)

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
            "Gateway connection (socket or URL) not configured for token counting"
        )

    # Convert container socket paths to host paths if using sockets
    if socket_path and socket_path.startswith("/sockets/"):
        socket_name = socket_path.split("/")[-1]
        socket_path = f"/tmp/universal-sockets/{socket_name}"
        logger.debug(
            f"Translated container socket path to host: /sockets/{socket_name} -> "
            f"{socket_path}"
        )

    return socket_path, http_url


async def _proxy_to_gateway(
    socket_path: str | None, http_url: str | None, body: dict[str, Any]
) -> dict[str, Any]:
    """
    Proxy token count request to local Gateway via Unix socket or HTTP.

    Translates field names from federation format to Gateway schema:
    - "model" → "model_name" (Gateway expects model_name)

    Args:
        socket_path: Unix socket path to gateway (if socket-based)
        http_url: HTTP URL to gateway (if HTTP-based)
        body: Request payload (model, messages/prompt)

    Returns:
        Token count response from gateway

    Raises:
        httpx.HTTPStatusError: On gateway error response
        httpx.RequestError: On connection failure
    """
    # Translate field names for Gateway schema compatibility
    gateway_body = dict(body)
    if "model" in gateway_body:
        gateway_body["model_name"] = gateway_body.pop("model")

    # Determine endpoint and transport
    if socket_path:
        gateway_url = "http://gateway"
        transport = httpx.AsyncHTTPTransport(uds=socket_path)
        logger.debug(f"🔌 Using Unix socket: {socket_path}")
    elif http_url:
        gateway_url = http_url.rstrip("/")
        transport = None
        logger.debug(f"🔌 Using HTTP: {gateway_url}")
    else:
        raise ValueError("Either socket_path or http_url must be provided")

    token_endpoint = f"{gateway_url}/api/v1/tokens/count"
    logger.debug(f"🎯 Token endpoint: {token_endpoint}")

    async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
        logger.debug(
            f"📤 Sending token count request to Gateway: "
            f"endpoint={token_endpoint}, model={body.get('model')}"
        )
        response = await client.post(
            token_endpoint,
            json=gateway_body,
            headers={"Content-Type": "application/json"},
        )
        logger.debug(f"📥 Gateway response: status={response.status_code}")
        _ = response.raise_for_status()
        return response.json()


def create_federation_token_router(
    config: FederationConfig,
    gateway_socket_path: str | None = None,
    gateway_url: str | None = None,
    local_edge_client=None,  # For relay forwarding to Edge
) -> APIRouter:
    """
    Create federation token counting router.

    PURE PROXY: Does not orchestrate model loading.
    Master calls /api/v1/federation/models/load before calling this endpoint.

    STRUCTURAL ENFORCEMENT: This function signature does NOT accept model_manager.
    If you need orchestration, you're in the wrong file - use federation/api/models.py.

    Args:
        config: Federation configuration
        gateway_socket_path: Explicit socket path (Edge/Master modes with Unix socket)
        gateway_url: Explicit HTTP URL (Edge/Master modes with HTTP)
        local_edge_client: Optional LocalEdgeClient for relay topology forwarding

    Returns:
        APIRouter with token counting endpoint
    """
    router = APIRouter(prefix="/api/v1/federation/tokens", tags=["federation-tokens"])

    @router.post("/count")
    async def count_tokens_federated(request: Request) -> dict[str, Any]:
        """
        Count tokens via local Gateway or forward to Edge.

        PURE PROXY: Assumes model is already loaded.
        Master handles orchestration before calling this endpoint.

        Relay Topology (Remote with local_edge):
            Remote → Edge (via Unix socket) → Gateway (inside Edge container)

        Direct Topology (Edge/Master with Gateway):
            Edge/Master → Gateway (via direct connection)

        Request body:
            model: str - Model ID
            messages: list[dict] - Chat messages (or prompt: str)

        Returns:
            Token count result from Gateway
        """
        body = await request.json()
        model_id = body.get("model")

        if not model_id:
            raise HTTPException(status_code=422, detail="'model' field required")

        logger.debug(
            f"📊 Token count request received: model={model_id}, "
            f"has_local_edge={local_edge_client is not None}"
        )

        # Relay topology: Forward to Edge via Unix socket
        if local_edge_client:
            logger.debug(
                f"📡 Forwarding token count to Edge via Unix socket: {model_id}"
            )
            try:
                # Build federation auth headers for Edge authentication
                headers = {
                    "Content-Type": "application/json",
                }
                if config and config.stargate_id:
                    headers["X-Federation-Source"] = config.stargate_id
                    headers["X-Federation-Key"] = local_edge_client._config.api_key

                # Edge exposes /api/v1/federation/tokens/count endpoint
                # Placeholder URL 'http://edge' - socket determines connection
                async with httpx.AsyncClient(
                    transport=httpx.AsyncHTTPTransport(
                        uds=local_edge_client._config.socket_path
                    ),
                    timeout=30.0,
                ) as client:
                    response = await client.post(
                        "http://edge/api/v1/federation/tokens/count",
                        json=body,
                        headers=headers,
                    )
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"❌ Edge token count failed: HTTP {e.response.status_code}"
                )
                raise HTTPException(
                    status_code=e.response.status_code,
                    detail=f"Edge token count failed: {e.response.text}",
                )
            except Exception as e:
                logger.exception(f"❌ Failed to forward token count to Edge: {e}")
                raise HTTPException(503, f"Edge connection failed: {e}")

        # Direct topology: Connect to Gateway directly
        logger.debug(
            f"📊 Resolving gateway connection: "
            f"gateway_socket_path={gateway_socket_path}, gateway_url={gateway_url}"
        )
        try:
            sock_path, http_url = _resolve_gateway_connection(
                config, gateway_socket_path, gateway_url
            )
        except ValueError as e:
            logger.error(f"❌ Failed to resolve gateway connection: {e}")
            raise HTTPException(status_code=503, detail=str(e))

        conn_type = "socket" if sock_path else "HTTP"
        target = sock_path if sock_path else http_url
        logger.info(f"📊 Token counting for {model_id} via {conn_type} to {target}")

        try:
            result = await _proxy_to_gateway(sock_path, http_url, body)
            logger.debug(
                f"✅ Token count successful: {result.get('token_count')} tokens"
            )
            return result
        except httpx.HTTPStatusError as e:
            logger.error(
                f"❌ Gateway error during token counting: HTTP {e.response.status_code}"
            )
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Gateway error: {e.response.text}",
            )
        except httpx.RequestError as e:
            logger.error(f"❌ Connection error to gateway: {e}")
            raise HTTPException(
                status_code=503,
                detail=f"Cannot connect to gateway: {e}",
            )

    return router
